"""
File       : UnifiedTransferorManager.py
Author     : Valentin Kuznetsov <vkuznet AT gmail dot com>
Description: UnifiedTransferorManager class provides full functionality of the UnifiedTransferor service.
"""

# futures
from __future__ import print_function, division

# system modules
import time
import logging
import hashlib
import tempfile
import traceback

from httplib import HTTPException

# WMCore modules
from WMCore.MicroService.Unified.Common import uConfig
from WMCore.MicroService.Unified.RequestInfo import requestsInfo
from WMCore.MicroService.Unified.TaskManager import start_new_thread
from WMCore.Services.PhEDEx.PhEDEx import PhEDEx
from WMCore.Services.PhEDEx.DataStructs.SubscriptionList import PhEDExSubscription
from WMCore.Services.ReqMgrAux.ReqMgrAux import ReqMgrAux
from WMCore.Services.ReqMgr.ReqMgr import ReqMgr

def requestRecord(data, reqStatus):
    "Return valid fields from reqmgr2 record which we'll use in MS transferor"
    siteWhiteList = data.get('SiteWhitelist', [])
    siteBlackList = data.get('SiteBlacklist', [])
    tasks = [k for k in data.keys() if k.startswith('Task') and not k.endswith('Chain')]
    datasets = []
    for task in tasks:
        for key in ['InputDataset', 'MCPileup']:
            dataset = data[task].get(key, '')
            if dataset:
                datasets.append({'type': key, 'name': dataset})
    name = data.get('_id', '')
    if not name:
        raise Exception("request record does not provide _id")
    return {'name': name, 'reqStatus': reqStatus,
            'SiteWhiteList': siteWhiteList,
            'SiteBlackList': siteBlackList,
            'datasets': datasets}

def daemon(func, reqStatus, interval):
    "Daemon to perform given function action for all request in our store"
    while True:
        try:
            func(reqStatus)
        except Exception as exc:
            pass
        time.sleep(interval)

class MSManager(object):
    "Class to keep track of transfer progress in PhEDEx for a given task"
    def __init__(self, svc, group='DataOps', dbFileName=None, interval=60, logger=None, verbose=False):
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger('reqmgr2ms:MSManager')
            logging.basicConfig()
        self.verbose = verbose
        if verbose:
            self.logger.setLevel(logging.DEBUG)
        if not dbFileName:
            fobj = tempfile.NamedTemporaryFile()
            dbFileName = '%s.db' % fobj.name
        self.logger.debug("dbFileName %s" % dbFileName)
        self.phedex = PhEDEx() # eventually will change to Rucio
        self.group = group
        self.svc = svc # Services: ReqMgr, ReqMgrAux
        thname = 'MSTransferor'
        self.thr = start_new_thread(thname, daemon, (self.transferor, 'assigned', interval))
        self.logger.debug("### Running %s thread %s" % (thname, self.thr.running()))
        thname = 'MSTransferorMonit'
        self.ms_monit = start_new_thread(thname, daemon, (self.monit, 'staging', interval))
        self.logger.debug("+++ Running %s thread %s" % (thname, self.ms_monit.running()))
        self.logger.info("MSManager, group=%s, db=%s, interval=%s" % (group, dbFileName, interval))

    def monit(self, reqStatus='staging'):
        """
        MSManager monitoring function.
        It performs transfer requests from staging to staged state of ReqMgr2.
        For references see
        https://github.com/dmwm/WMCore/wiki/ReqMgr2-MicroService-Transferor
        """
        try:
            # get requests from ReqMgr2 data-service for given statue
            requestSpecs = self.svc.reqmgr.getRequestByStatus([reqStatus], detail=False)
            requestRecords = [requestRecord(r, reqStatus) for r in requestSpecs]
            self.logger.debug('+++ monit found %s requests in %s state' % (len(requestRecords), reqStatus))

            requestStatus = {} # keep track of request statuses
            for rec in requestRecords:
                reqName = rec['name']
                # get transfer IDs
                tids = self.getTransferIDs()
                # get transfer status
                transferStatuses = self.getTransferStatuses(tids)
                # get campaing and unified configuration
                campain = self.requestCampaign(reqName)
                conf = self.requestConfiguration(reqName)

                # if all transfers are completed, move the request status staging -> staged
                # completed = self.checkSubscription(request)
                completed = 100 # TMP
                if completed == 100: # all data are staged
                    self.logger.debug("+++ request %s all transfers are completed" % req)
	            self.change(req, 'staged', '+++ monit')
                # if pileup transfers are completed AND some input blocks are completed, move the request status staging -> staged
                elif self.pileupTransfersCompleted(tids):
                    self.logger.debug("+++ request %s pileup transfers are completed" % req)
	            self.change(req, 'staged', '+++ monit')
                # transfers not completed, just update the database with their completion
                else:
                    self.logger.debug("+++ request %s transfers are not completed" % req)
                    requestStatus[req] = transferStatuses # TODO: implement update of transfer ids
            self.updateTransferIDs(requestStatus)
	except Exception as err: # general error
            self.logger.error(err)

    def transferor(self, reqStatus='assigned'):
        """
        MSManager transferor function.
        It performs Unified logic for data subscription and 
        transfers requests from assigned to staging/staged state of ReqMgr2.
        For references see
        https://github.com/dmwm/WMCore/wiki/ReqMgr2-MicroService-Transferor
        """
        try:
            # get requests from ReqMgr2 data-service for given statue
            requestSpecs = self.svc.reqmgr.getRequestByStatus([reqStatus], detail=True)
            requestRecords = [requestRecord(r, reqStatus) for r in requestSpecs]
            self.logger.debug('+++ monit found %s requests in %s state' % (len(requestRecords), reqStatus))
            # get complete requests information (based on Unified Transferor logic)
            requestRecords = requestsInfo(requestRecords, self.svc, self.logger, self.verbose)
	except Exception as err: # general error
            self.logger.error(err)
            requests = []
        # process all requests
        for rec in requestRecords:
            reqName = rec['name']
            # perform transfer
            tid = self.transferRequest(rec)
            if tid:
                # Once all transfer requests were successfully made, update: assigned -> staging
                self.logger.debug("### transfer request for %s successfull" % reqName)
                self.change(req, 'stag', '### transferor')
            # if there is nothing to be transferred (no input at all),
            # then update the request status once again staging -> staged
            # self.change(req, 'staged', '### transferor')

    def stop(self):
        "Stop MSManager"
        # stop MSTransferorMonit thread
        self.ms_monit.stop()
        # stop MSTransferor thread
        self.thr.stop() # stop checkStatus thread
        status = self.thr.running()
        return status

    def transferRequest(self, req):
        "Send request to Phedex and return status of request subscription"
	datasets = req.get('datasets', [])
	sites = req.get('sites', [])
        if datasets and sites:
	    subscription = PhEDExSubscription(datasets, sites, self.group)
	    self.logger.debug("### add subscription %s for request %s" % (subscription, request))
            # TODO: implement how to get transfer id
            tid = hashlib.md5(str(subscription)).hexdigest()
	    # TODO: when ready enable submit subscription step
	    # self.phedex.subscribe(subscription)
            return tid

    def getTransferIDsDoc(self):
        """
        Get transfer ids document from backend. The document has the following form:
        https://gist.github.com/amaltaro/72599f995b37a6e33566f3c749143154
	{"wf_A": {"timestamp": 0000
		  "primary": {"dset_1": ["list of transfer ids"]},
		  "secondary": {"PU_dset_1": ["list of transfer ids"]},
	 "wf_B": {"timestamp": 0000
		  "primary": {"dset_1": ["list of transfer ids"],
			      "parent_dset_1": ["list of transfer ids"]},
		  "secondary": {"PU_dset_1": ["list of transfer ids"],
				"PU_dset_2": ["list of transfer ids"]},
         ...
	}
        """
        doc = {}
        return doc

    def updateTransferIDs(self, requestStatus):
        "Update transfer ids in backend"
        # TODO/Wait: https://github.com/dmwm/WMCore/issues/9198
        doc = self.getTransferIDsDoc()
        pass

    def getTransferIDs(self):
        "Get transfer ids from backend"
        # TODO/Wait: https://github.com/dmwm/WMCore/issues/9198
        # meanwhile return transfer ids from internal store 
        return tids

    def getTransferStatuses(self, tids):
        "get transfer statuses for given transfer IDs from backend"
        # transfer docs on backend has the following form
        # https://gist.github.com/amaltaro/72599f995b37a6e33566f3c749143154
        statuses = {}
        for tid in tids:
            # TODO: I need to find request name from transfer ID
            #status = self.checkSubscription(request)
            status = 100
            statuses[tid] = status
        return statuses

    def requestCampaign(self, req):
        "Return request campaign"
        pass

    def requestConfiguration(self, req):
        "Return request configuration"
        pass

    def pileupTransfersCompleted(self, tids):
        "Check if pileup transfers are completed"
        # TODO: add implementation
        return False

    def checkSubscription(self, req):
        "Send request to Phedex and return status of request subscription"
        sdict = {}
        for dataset in req.get('datasets', []):
            data = self.phedex.subscriptions(dataset=dataset, group=self.group)
            self.logger.debug("### dataset %s group %s" % (dataset, self.group))
            self.logger.debug("### subscription %s" % data)
            for row in data['phedex']['dataset']:
                if row['name'] != dataset:
                    continue
                nodes = [s['node'] for s in row['subscription']]
                rNodes = req.get('sites')
                self.logger.debug("### nodes %s %s" % (nodes, rNodes))
                subset = set(nodes) & set(rNodes)
                if subset == set(rNodes):
                    sdict[dataset] = 1
                else:
                    pct = float(len(subset))/float(len(set(rNodes)))
                    sdict[dataset] = pct
        self.logger.debug("### sdict %s" % sdict)
        tot = len(sdict.keys())
        if not tot:
            return -1
        # return percentage of completion
        return round(float(sum(sdict.values()))/float(tot), 2) * 100

    def checkStatus(self, req):
        "Check status of request in local storage"
        self.logger.debug("### checkStatus of request: %s" % req['name'])
        # check subscription status of the request
        # completed = self.checkSubscription(request)
        completed = 100
        if completed == 100: # all data are staged
            self.logger.debug("### request is completed, change its status and remove it from the store")
            self.change(req, 'staged', '### transferor')
        else:
            self.logger.debug("### request %s, completed %s" % (request, completed))

    def change(self, req, reqStatus, prefix='###'):
        """
        Change request status, internally it is done via PUT request to ReqMgr2:
        curl -X PUT -H "Content-Type: application/json" \
             -d '{"RequestStatus":"staging", "RequestName":"bla-bla"}' \
             https://xxx.yyy.zz/reqmgr2/data/request
        """
        try:
            self.logger.debug('%s changing %s status of record %s to reqStatus=%s' \
                % (prefix, req['name'], req, reqStatus))
            if req.get('reqStatus', None) != reqStatus:
                self.svc.reqmgr.updateRequestStatus(req['name'], reqStatus)
        except HTTPException as err:
            traceback.print_exc()
            self.logger.error('%s change: url=%s headers=%s status=%s reason=%s' \
                % (prefix, err.url, err.headers, err.status, err.reason))
        except Exception as err:
            traceback.print_exc()

    def info(self, req):
        "Return info about given request"
        completed = self.checkSubscription(req)
        return {'request': req, 'status': completed}

    def delete(self, request):
        "Delete request in backend"
        pass

class Services(object):
    "Services class provides access to reqmgr2 services: ReqMgr, ReqMgrAux"
    __slots__ = ('reqmgrAux', 'reqmgr', 'workqueue')
    def __init__(self, reqmgrUrl, logger=None):
        self.reqmgrAux = ReqMgrAux(reqmgrUrl, logger=logger)
        self.reqmgr = ReqMgr(reqmgrUrl, logger=logger)

class UnifiedTransferorManager(object):
    """
    UnifiedTransferorManager class provides an REST interface to reqmgr2ms service.
    """
    def __init__(self, config=None, logger=None):
        self.config = config
        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger('reqmgr2ms:%s' % self.__class__.__name__)
            logging.basicConfig()
        reqmgrUrl = getattr(config, 'reqmgrUrl', 'https://cmsweb.cern.ch/reqmgr2')
        self.svc = Services(reqmgrUrl, self.logger)
        group = getattr(config, 'group', 'DataOps')
        interval = getattr(config, 'interval', 3600)
        self.verbose = getattr(config, 'verbose', False)
        # update uConfig urls according reqmgr2ms configuration
        uConfig.set('reqmgrUrl', reqmgrUrl)
        uConfig.set('reqmgrCacheUrl', getattr(config, 'reqmgrCacheUrl', 'https://cmsweb.cern.ch/couchdb/reqmgr_workload_cache'))
        self.msManager = MSManager(self.svc, group, interval, logger, self.verbose)

    def status(self):
        "Return current status about UnifiedTransferor"
        sdict = {}
        return sdict
