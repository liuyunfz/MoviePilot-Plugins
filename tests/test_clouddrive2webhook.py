import asyncio
import json
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from plugin_test_support import load_plugin

module = load_plugin('clouddrive2webhook')


def test_path_boundaries():
    cls = module.CloudDrive2Webhook
    assert cls._path_in_dir('/upload/movie.mkv', Path('/upload'))
    assert cls._path_in_dir('/upload/movie.mkv', Path('/'))
    assert not cls._path_in_dir('/upload-other/movie.mkv', Path('/upload'))
    assert not cls._path_in_dir('/upload/../private/movie.mkv', Path('/upload'))


def test_busy_and_unmatched_events_do_not_notify(monkeypatch):
    plugin = module.CloudDrive2Webhook()
    plugin.init_plugin({'enabled':True, 'monitor_path':'/upload'})
    called = []
    monkeypatch.setattr(plugin, '_notify_cms', lambda **kwargs: called.append(kwargs))
    monkeypatch.setattr(plugin, '_has_running_organize_task', lambda: True)
    class Request:
        headers = {}
        client = None
        async def json(self):
            return {'event_category':'file','event_name':'notify','data':[{'action':'create','source_file':'/upload/file.mkv'}]}
    result = asyncio.run(plugin.handle_file_notify(Request()))
    assert result['message'] == 'skipped_busy'
    assert called == []
    plugin._monitor_path = '/other'
    assert asyncio.run(plugin.handle_file_notify(Request()))['message'] == 'ignored'
    assert called == []


def test_cms_credentials_never_enter_result_or_logs(monkeypatch, caplog):
    plugin = module.CloudDrive2Webhook()
    plugin.init_plugin({'cms_domain':'https://cms.example','cms_api_token':'fixture-private-secret'})
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.status = 200
    response.read.return_value = b'echo fixture-private-secret'
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: response)
    result = plugin._notify_cms({}, 'notify', [])
    assert result['success']
    assert 'fixture-private-secret' not in json.dumps(result) + caplog.text
    for error in (urllib.error.HTTPError('https://cms.example/?token=fixture-private-secret',403,'denied',{},None), ValueError('fixture-private-secret')):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(module.urllib.request, 'urlopen', fail)
        result = plugin._notify_cms({}, 'notify', [])
        assert not result['success']
        assert 'fixture-private-secret' not in json.dumps(result) + caplog.text
