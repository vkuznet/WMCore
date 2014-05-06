#!/usr/bin/env python
#-*- coding: utf-8 -*-
#pylint: disable=
"""
File       : SummaryDB.py
Author     : Valentin Kuznetsov <vkuznet AT gmail dot com>
Description: Set of modules/functions to update WMAgent SummaryDB.
"""

# system modules
import os
import sys
import json
import logging
from pprint import pformat

# CMS modules
from WMCore.Database.CMSCouch import CouchServer, CouchNotFoundError

def fwjr_parser(doc):
    """
    Parse FWJR document and yeild the following structure:
    {"id": "requestName",
        "tasks": {
            "taskName1": {
                "sites": {
                    "siteName1":  {
                        "wrappedTotalJobTime": 1612,
                        "cmsRunCPUPerformance": 
                           {"totalJobCPU": 20, "totalJobTime": 42, "totalEventCPU": 4},
                        "inputEvents": 0,
                        "dataset": {}
                    }
                    "siteName2":  {
                        "wrappedTotalJobTime": 1612,
                        "cmsRunCPUPerformance": {"totalJobCPU": 20, "totalJobTime": 42, "totalEventCPU": 4},
                        "inputEvents": 0,
                        "dataset": {
                             "/a/b/c": {"size": 50, "events": 10, "totalLumis": 100},
                             "/a1/b1/c1": {"size": 50, "events": 10, "totalLumis": 100}
                        },
                    }
                },
            }
            "taskName2" : {}
        }
    }
    """

    if  'fwjr' in doc:
        fwjr = doc['fwjr']
        if  not isinstance(fwjr, dict):
            fwjr = fwjr.__to_json__(None)
    else:
        raise Exception('Document does not contain FWJR part')

    task_name = fwjr['task']
    try:
        req_name = task_name.split('/')[1]
    except:
        raise Exception('Cannot get request name from task_name "%s"' % task_name)

    # main loop
    sdict = {} # all sites summary
    steps = fwjr['steps']
    for key, val in steps.items():
        if  key.startswith('cmsRun'):
            if  'stop' in val and 'start' in val:
                wrappedTotalJobTime = val['stop'] - val['start']
            else:
                wrappedTotalJobTime = 0
            site_name = val['site']
            pdict = dict(totalJobCPU=0, totalJobTime=0, totalEventCPU=0)
            ddict = {}
            site_summary = dict(\
                    wrappedTotalJobTime=wrappedTotalJobTime,
                    inputEvents=0, dataset=ddict,
                    cmsRunCPUPerformance=pdict)

            perf = val['performance']
            pdict['totalJobCPU'] += float(perf['cpu']['TotalJobCPU'])
            pdict['totalJobTime'] += float(perf['cpu']['TotalJobTime'])
            pdict['totalEventCPU'] += float(perf['cpu']['TotalEventCPU'])
            odict = val['output']
            for kkk, vvv in odict.items():
                for row in vvv:
                    if  row['merged']:
                        prim = row['dataset']['primaryDataset']
                        proc = row['dataset']['primaryDataset']
                        tier = row['dataset']['primaryDataset']
                        dataset = '/'.join([prim, proc, tier])
                        totalLumis = sum([len(r) for r in row['runs']])
                        dataset_summary = \
                                dict(size=row['size'], events=row['events'], totalLumis=totalLumis)
                        ddict[dataset] = dataset_summary
            if  ddict: # if we got dataset summary
                site_summary.update({'dataset': ddict})

            idict = val.get('input', {})
            if  idict:
                source = idict.get('source', {})
                if  isinstance(source, list):
                    for item in source:
                        site_summary['inputEvents'] += item.get('events', 0)
                else:
                    site_summary['inputEvents'] += source.get('events', 0)
            sdict[site_name] = site_summary
    # prepare final data structure
    sum_doc = dict(_id=req_name, tasks={task_name: dict(sites=sdict)})
    return sum_doc

def merge_docs(doc1, doc2):
    "Merge two summary documents use dict in place strategy"
    for key in ['_id', '_rev']:
        if  key in doc1:
            del doc1[key]
        if  key in doc2:
            del doc2[key]
    for key, val in doc1.items():
        if  key not in doc2:
            return False
        if isinstance(val, dict):
            if  not merge_docs(doc1[key], doc2[key]):
                return False
        else:
            doc1[key] += doc2[key]
    return True

def update_tasks(old_tasks, new_tasks):
    "Update tasks dictionaries"
    for task, tval in new_tasks.items():
        if  task in old_tasks:
            for site, sval in tval['sites'].items():
                if  site in old_tasks[task]['sites']:
                    old_sdict = old_tasks[task]['sites'][site]
                    new_sdict = sval
                    status = merge_docs(old_sdict, new_sdict)
                    if  not status:
                        logging.error("Error merge_docs:\n%s\n%s\n" % (pformat(old_sdict), pformat(new_sdict)))
                else:
                    old_tasks[task]['sites'].update({site:sval})
        else:
            old_tasks.update({task: tval})
    return old_tasks

def updateSummaryDB(couchdb, document):
    "Update summary DB with given document"
    # connect to summary db
    dbname = 'wma_summarydb'
    try:
        sumdb = couchdb.connectDatabase(dbname, size = 250)
    except Exception as exc:
        logging.error("Error connecting to couch db '%s': %s" % (dbname, str(exc)))
        return False
    # parse input doc and create summary doc
    sum_doc = fwjr_parser(document)
    key_id = sum_doc['_id']
    # check if DB has FWJR statistics
    try:
        doc = sumdb.document(key_id)
    except CouchNotFoundError: # no document in DB
        sumdb.commitOne(sum_doc)
        return True
    except Exception as exc:
        print str(exc)
        logging.error("Error fetching summary doc %s from %s" % (key_id, dbname))
        return False
    # update summary DB
    rev = doc.pop('_rev')
    old_tasks = doc['tasks']
    new_tasks = sum_doc['tasks']
    tasks = update_tasks(old_tasks, new_tasks)
    sum_doc.update({'_rev':rev, 'tasks': tasks})
    sumdb.commitOne(sum_doc)

    # TODO: instead of using commitOne I can use updateDocument API
    # but it requires to have proper update_func in place, e.g.
    # sumdb.updateDocument(doc['_id'], design, update_func, fields={})

if __name__ == '__main__':
    couchurl = 'http://127.0.0.1:5984'
    couchdb = CouchServer(couchurl)
    updateSummaryDB(couchdb, json.load(open('fwjr.json', 'r')))
    updateSummaryDB(couchdb, json.load(open('fwjr_dataset.json', 'r')))
