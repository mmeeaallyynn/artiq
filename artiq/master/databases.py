import asyncio
from artiq.tools import file_import

from magic_os.database.mongo_tools import timeseries_collection, ExperimentData
from sipyco.sync_struct import Notifier, process_mod, update_from_dict
from sipyco import pyon
from sipyco.asyncio_tools import TaskObject
from eq_logger import eq_logger


logger = eq_logger.get_eq_logger(__name__)


def device_db_from_file(filename):
    mod = file_import(filename)

    # use __dict__ instead of direct attribute access
    # for backwards compatibility of the exception interface
    # (raise KeyError and not AttributeError if device_db is missing)
    return mod.__dict__["device_db"]


class DeviceDB:
    def __init__(self, backing_file):
        self.backing_file = backing_file
        self.data = Notifier(device_db_from_file(self.backing_file))

    def scan(self):
        update_from_dict(self.data, device_db_from_file(self.backing_file))

    def get_device_db(self):
        return self.data.raw_view

    def get(self, key, resolve_alias=False):
        desc = self.data.raw_view[key]
        if resolve_alias:
            while isinstance(desc, str):
                desc = self.data.raw_view[desc]
        return desc


def dataset_db_from_database():
    latest_states = timeseries_collection.aggregate([
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$metadata.experiment",
                "timestamp": {"$first": "$timestamp"},
                "data": {"$first": "$data"}
            }
        }
    ])

    data = dict()
    for item in latest_states:
        try:
            deserialized_data = ExperimentData.deserialize_data(item["data"])
            if "value" in deserialized_data:
                data[item["_id"]] = deserialized_data["value"]
        except Exception:
            logger.warning(f"Couldn't load dataset {item['_id']}")

    return data


class DatasetDB(TaskObject):
    def __init__(self, persist_file, autosave_period=30):
        self.persist_file = persist_file
        self.autosave_period = autosave_period

        try:
            file_data = dataset_db_from_database()
        except Exception:
            file_data = dict()
        self.data = Notifier({k: (True, v) for k, v in file_data.items()})

    def save(self):
        data = {k: v[1] for k, v in self.data.raw_view.items() if v[0]}
        pyon.store_file(self.persist_file, data)

    async def _do(self):
        try:
            while True:
                await asyncio.sleep(self.autosave_period)
                self.save()
        finally:
            self.save()

    def get(self, key):
        return self.data.raw_view[key][1]

    def update(self, mod):
        process_mod(self.data, mod)

    # convenience functions (update() can be used instead)
    def set(self, key, value, persist=None):
        if persist is None:
            if key in self.data.raw_view:
                persist = self.data.raw_view[key][0]
            else:
                persist = False
        self.data[key] = (persist, value)

    def delete(self, key):
        del self.data[key]
    #
