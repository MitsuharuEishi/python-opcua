from datetime import timedelta
from datetime import datetime
import time
import struct

from opcua import Subscription
from opcua import ua


def datetime_to_bytes(dt):
    time_float = time.mktime(dt.timetuple()) + dt.microsecond / 1E6
    return struct.pack("!L", time_float)


def bytes_to_datetime(data):
    time_float = struct.unpack('!L', data)[0]
    return datetime.fromtimestamp(time_float)


class HistoryStorageInterface(object):
    """
    Interface of a history backend
    """
    def new_node(self, node, period, count=0):
        raise NotImplementedError

    def save_datavalue(self, node, datavalue):
        raise NotImplementedError

    def read_datavalues(self, node, start, end, nb_values):
        raise NotImplementedError

    def new_event(self, event, period):
        raise NotImplementedError

    def save_event(self, event):
        raise NotImplementedError

    def read_events(self, start, end, evfilter):
        raise NotImplementedError


class HistoryDict(HistoryStorageInterface):
    """
    very minimal history backend storing data in memory using a Python dictionnary
    """
    def __init__(self):
        self._datachanges = {}
        self._datachanges_period = {}
        self._events = {}

    def new_node(self, node, period, count=0):
        self._datachanges[node] = []
        self._datachanges_period[node] = period, count

    def new_event(self, event, period):
        self._events = []

    def save_datavalue(self, node, datavalue):
        print("saving", node, datavalue)
        data = self._datachanges[node]
        period, count = self._datachanges_period[node]
        data.append(datavalue)
        now = datetime.now()
        if period:
            while now - data[0].ServerTimestamp > period:
                data.pop(0)
        if count and len(data) > count:
            data = data[-count:]

    def read_datavalues(self, node, start, end, nb_values):
        if node not in self._datachanges:
            return []
        else:
            # FIME: improve algo
            return [dv for dv in self._datachanges[node] if start <= dv.ServerTimestamp <= end]

    def save_event(self, timestamp, event):
        raise NotImplementedError

    def read_events(self, start, end, evfilter):
        raise NotImplementedError


class SubHandler(object):
    def __init__(self, storage):
        self.storage = storage

    def datachange_notification(self, node, val, data):
        self.storage.save_datavalue(node, data.monitored_item.Value)

    def event_notification(self, event):
        self.storage.save_event(event)


class HistoryManager(object):
    def __init__(self, iserver):
        self.iserver = iserver
        self.storage = HistoryDict()
        self._sub = None
        self._handlers = {}

    def set_storage(self, storage):
        self.storage = storage

    def _create_subscription(self, handler):
        params = ua.CreateSubscriptionParameters()
        params.RequestedPublishingInterval = 10
        params.RequestedLifetimeCount = 3000
        params.RequestedMaxKeepAliveCount = 10000
        params.MaxNotificationsPerPublish = 0
        params.PublishingEnabled = True
        params.Priority = 0
        return Subscription(self.iserver.isession, params, handler)

    def historize(self, node, period=timedelta(days=7), count=0):
        if not self._sub:
            self._sub = self._create_subscription(SubHandler(self.storage))
        if node in self._handlers:
            raise ua.UaError("Node {} is allready historized".format(node))
        self.storage.new_node(node, period, count)
        handler = self._sub.subscribe_data_change(node)
        self._handlers[node] = handler

    def dehistorize(self, node):
        self._sub.unsubscribe(self._handlers[node])
        del(self._handlers[node])

    def read_history(self, params):
        """
        Read history for a node
        This is the part AttributeService, but implemented as its own service
        since it requires more logic than other attribute service methods
        """
        results = []
        
        for rv in params.NodesToRead:
            res = self._read_history(params.HistoryReadDetails, rv)
            results.append(res)
        return results
        
    def _read_history(self, details, rv):
        """ read history for a node 
        """
        result = ua.HistoryReadResult()
        if type(details) is ua.ReadRawModifiedDetails:
            if details.IsReadModified:
                result.HistoryData = ua.HistoryModifiedData()
                # we do not support modified history by design so we return what we have
                dv, cont = self._read_datavalue_history(rv, details)
                result.HistoryData.DataValues = dv
                result.ContinuationPoint = cont
            else:
                result.HistoryData = ua.HistoryData()
                dv, cont = self._read_datavalue_history(rv, details)
                result.HistoryData.DataValues = dv
                result.ContinuationPoint = cont

        elif type(details) is ua.ReadEventDetails:
            result.HistoryData = ua.HistoryEvent()
            # FIXME: filter is a cumbersome type, maybe transform it something easier
            # to handle for storage
            result.HistoryData.Events = self.storage.read_events(details.StartTime,
                                                                 details.EndTime,
                                                                 details.Filter)
        else:
            # we do not currently support the other types, clients can process data themselves
            result.StatusCode = ua.StatusCode(ua.StatusCodes.BadNotImplemented)
        return result

    def read_datavalue_history(self, rv, details):
        starttime = details.StartTime
        if rv.ContinuationPoint:
            # Spec says we should ignore details if cont point is present
            # but they also say we can use cont point as timestamp to enable stateless
            # implementation. This is contradictory, so we assume details is
            # send correctly with continuation point
            starttime = bytes_to_datetime(rv.ContinuationPoint)

        dv, cont = self.storage.read_datavalues(rv.NodeId,
                                                starttime,
                                                details.EndTime,
                                                details.NumValuesPerNode)
        if cont:
            cont = datetime_to_bytes(dv[-1].SourceTimeStamp)
        # FIXME, parse index range and filter out if necesary
        # rv.IndexRange
        # rv.DataEncoding # xml or binary, seems spec say we can ignore that one
        return dv, cont

    def update_history(self, params):
        """
        Update history for a node
        This is the part AttributeService, but implemented as its own service
        since it requires more logic than other attribute service methods
        """
        results = []
        for details in params.HistoryUpdateDetails:
            result = ua.HistoryUpdateResult()
            # we do not accept to rewrite history
            result.StatusCode = ua.StatusCode(ua.StatusCodes.BadNotWritable)
            results.append(results)
        return results




