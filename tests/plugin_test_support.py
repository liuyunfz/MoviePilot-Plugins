"""Offline MoviePilot boundary doubles for plugin release checks."""
import copy
import importlib.util
import logging
import sys
import types
from pathlib import Path


class PluginBase:
    def __init__(self):
        self.data = {}
        self.saved_config = {}
        self.messages = []

    def get_data(self, key):
        return copy.deepcopy(self.data.get(key))

    def save_data(self, key, value):
        self.data[key] = copy.deepcopy(value)

    def update_config(self, config):
        self.saved_config = copy.deepcopy(config)

    def get_config(self):
        return self.saved_config

    def post_message(self, **kwargs):
        self.messages.append(kwargs)


class ProgressHelper:
    def get(self, *args):
        return {}


def load_plugin(name):
    values = {
        'app': {}, 'app.core': {}, 'app.helper': {}, 'app.utils': {}, 'app.db': {},
        'app.core.config': {'settings': types.SimpleNamespace(TZ='Asia/Shanghai', VERSION_FLAG='v2', PROXY={})},
        'app.plugins': {'_PluginBase': PluginBase},
        'app.log': {'logger': logging.getLogger('plugin-release-test')},
        'app.schemas': {'NotificationType': types.SimpleNamespace(SiteMessage='site')},
        'app.schemas.types': {'ProgressKey': types.SimpleNamespace(FileTransfer='transfer')},
        'app.helper.progress': {'ProgressHelper': ProgressHelper},
        'app.helper.cloudflare': {'under_challenge': lambda *args: False},
        'app.utils.http': {'RequestUtils': object},
        'app.db.site_oper': {'SiteOper': object},
    }
    saved = {name: sys.modules.get(name) for name in values}
    try:
        for key, attrs in values.items():
            sys.modules[key] = types.ModuleType(key)
            vars(sys.modules[key]).update(attrs)
        path = Path(__file__).resolve().parents[1] / 'plugins' / name
        module_name = '_release_test_' + name
        spec = importlib.util.spec_from_file_location(module_name, path / '__init__.py', submodule_search_locations=[str(path)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, previous in saved.items():
            if previous is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = previous
