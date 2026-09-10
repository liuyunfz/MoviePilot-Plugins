import importlib
import types

from plugin_test_support import load_plugin

module = load_plugin('ptautotask')
base = importlib.import_module(module.__name__ + '.base.BaseTask')
decorator = importlib.import_module(module.__name__ + '.base.Decorator').task_info


class FixtureTasks(base.BaseTask):
    cookies = []

    def __init__(self, cookie=None):
        super().__init__(types.SimpleNamespace(name_cn='Fixture'))
        self.cookie = cookie

    @decorator(label='领取任务')
    def claim(self):
        self.cookies.append(self.cookie)
        return self.fail('认领人数已达上限')


def test_runner_unwraps_tasks_uses_site_cookie_and_honors_failure(monkeypatch):
    plugin = module.PTAutoTask()
    tasks = FixtureTasks().get_registered_tasks()
    assert len(tasks) == 1
    setattr(plugin, tasks[0]['id'], True)
    plugin._retry_count = 2
    plugin._current_retry = 0
    plugin._retry_interval = 1
    plugin._notify = True
    schedules, records, messages = [], [], []
    monkeypatch.setattr(plugin, 'get_filter_sites', lambda: [{'name':'fixture', 'domain':'fixture.example', 'cookie':'fixture-cookie', 'tasks':tasks}])
    monkeypatch.setattr(plugin, '_schedule_retry', lambda: schedules.append(True))
    monkeypatch.setattr(plugin, '_save_history_run', lambda rows: records.append(rows))
    monkeypatch.setattr(plugin, '_send_notification', lambda title, text: messages.append(text))
    FixtureTasks.cookies = []
    for _ in range(4):
        plugin._PTAutoTask__do_tasks()
    assert FixtureTasks.cookies == ['fixture-cookie'] * 4
    assert len(schedules) == 2
    assert all(not run[0]['success'] for run in records)
    assert records[0][0]['status'] == '认领人数已达上限'
    assert all('❌' in message and 'TaskResult(' not in message for message in messages)


def test_result_normalizes_failure_and_exception():
    task = FixtureTasks()
    for raw in (False, None, (False, 'rejected'), {'success':False, 'message':'denied'}, ValueError('failed')):
        assert not task._normalize_task_result(raw).success
    assert task._normalize_task_result(task.ok(message='done')).message == 'done'
    assert task.get_registered_tasks()[0]['func']().success is False
