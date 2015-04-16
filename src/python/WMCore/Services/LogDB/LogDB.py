#!/usr/bin/env python
"""
LogDB provides functionality to post/search messages into LogDB.
https://github.com/dmwm/WMCore/issues/5705
"""

# standard modules
import os
import logging
import threading

# project modules
from WMCore.Services.LogDB.LogDBBackend import LogDBBackend, clean_entry
from WMCore.Lexicon import splitCouchServiceURL
from WMCore.Database.CMSCouch import CouchNotFoundError

class LogDB(object):
    """
    _LogDB_

    LogDB object - interface to LogDB functionality.
    """
    def __init__(self, url, identifier, centralurl=None, logger=None, **kwds):
        self.logger = logger if logger else logging.getLogger()
        if  not url or not identifier:
            raise RuntimeError("Attempt to init LogDB with url='%s', identifier='%s'"\
                    % (url, identifier))
        self.identifier = identifier
        try:
            self.thread_name = kwds.pop('thread_name')
        except KeyError:
            self.thread_name = threading.currentThread().getName()
        self.agent = 1 if centralurl else 0
        self.localurl = url
        self.centralurl = centralurl
        couch_url, db_name = splitCouchServiceURL(self.localurl)
        self.backend = LogDBBackend(couch_url, db_name, identifier, self.thread_name, self.agent, **kwds)
        self.central = None
        if  centralurl:
            couch_url, db_name = splitCouchServiceURL(self.centralurl)
            self.central = LogDBBackend(couch_url, db_name, identifier, self.thread_name, self.agent, **kwds)
        self.logger.info(self)

    def __repr__(self):
        "Return representation for class"
        return "<LogDB(local=%s, central=%s, agent=%s)>" \
                % (self.localurl, self.centralurl, self.agent)

    def post(self, request, msg, mtype="comment"):
        """Post new entry into LogDB for given request"""
        try:
            res = self.backend.post(request, msg, mtype)
        except Exception as exc:
            self.logger.error("LogDBBackend post API failed, error=%s" % str(exc))
            res = 'post-error'
        self.logger.debug("LogDB post request, res=%s", res)
        return res

    def get(self, request, mtype="comment"):
        """Retrieve all entries from LogDB for given request"""
        try:
            res = [clean_entry(r['doc']) for r in \
                    self.backend.get(request, mtype).get('rows', [])]
        except Exception as exc:
            self.logger.error("LogDBBackend get API failed, error=%s" % str(exc))
            res = 'get-error'
        self.logger.debug("LogDB get request, res=%s", res)
        return res

    def get_all_requests(self):
        """Retrieve all entries from LogDB for given request"""
        try:
            results = self.backend.get_all_requests()
            res = []
            for row in results['rows']:
                res.append(row["key"])
        except Exception as exc:
            self.logger.error("LogDBBackend get_all_requests API failed, error=%s" % str(exc))
            res = 'get-error'
        self.logger.debug("LogDB get_all_requests request, res=%s", res)
        return res

    def delete(self, request):
        """Delete entry in LogDB for given request"""
        try:
            res = self.backend.delete(request)
        except Exception as exc:
            self.logger.error("LogDBBackend delete API failed, error=%s" % str(exc))
            res = 'delete-error'
        self.logger.debug("LogDB delete request, res=%s", res)
        return res

    def summary(self, request):
        """Generate summary document for given request"""
        try:
            res = self.backend.summary(request)
        except Exception as exc:
            self.logger.error("LogDBBackend summary API failed, error=%s" % str(exc))
            res = 'summary-error'
        self.logger.debug("LogDB summary request, res=%s", res)
        return res

    def upload2central(self, request):
        """
        Upload local LogDB docs corresponding to given request
        into central LogDB database
        """
        if  not self.central:
            if  self.logger:
                self.logger.debug("LogDB upload2central does nothing, no central setup")
            return -1
        try:
            docs = self.backend.summary(request)
            for doc in docs:
                try:
                    exist_doc = self.central.db.document(doc["_id"])
                    doc["_rev"] = exist_doc["_rev"]
                except CouchNotFoundError:
                    self.logger.debug("initial update for %s" % doc["_id"])
                
                self.central.db.queue(doc)
            res = self.central.db.commit()
        except Exception as exc:
            self.logger.error("LogDBBackend summary API failed, error=%s" % str(exc))
            res = 'summary-error'
        self.logger.debug("LogDB upload2central request, res=%s", res)
        return res

    def cleanup(self, thr, backend='local'):
        """Clean-up back-end LogDB"""
        try:
            if  backend=='local':
                self.backend.cleanup(thr)
            elif backend=='central':
                self.central.cleanup(thr)
            else:
                raise RuntimeError()
        except Exception as exc:
            self.logger.error('LogDBBackend cleanup API failed, backend=%s, error=%s' \
                    % (backend, str(exc)))

    def report(self, request, print_report=False):
        """Provide human readable report for given request"""
        data = self.summary(request)
        if  not isinstance(data, list):
            msg = "There is no data available with request: '%s'" % request
            msg += 'Collected summary: %s' % data
            return msg
        odict = {}
        for item in data:
            odict[item['ts']] = item
        keys = sorted(odict.keys())
        keys.reverse()
        times = []
        messages = []
        mtypes = []
        for key in keys:
            val = odict[key]
            times.append(str(key))
            messages.append(val['msg'])
            mtypes.append(val['type'])
        tstpad = max([len(t) for t in times])
        msgpad = max([len(m) for m in messages])
        mtppad = max([len(m) for m in mtypes])
        out = []
        if  print_report:
            msg = 'Report for %s' % request
            print msg, '\n', '-'*len(msg)
        for idx in range(len(times)):
            tcol = '%s%s' % (times[idx], ' '*(tstpad-len(times[idx])))
            mcol = '%s%s' % (messages[idx], ' '*(msgpad-len(messages[idx])))
            ecol = '%s%s' % (mtypes[idx], ' '*(mtppad-len(mtypes[idx])))
            if  print_report:
                print "%s %s %s" % (tcol, mcol, ecol)
            else:
                out.append((tcol, mcol, ecol))
        if  not print_report:
            return out
