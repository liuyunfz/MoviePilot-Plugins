from plugin_test_support import load_plugin

module = load_plugin('nodeseeksign')


def test_scheduler_and_legacy_config_preservation(monkeypatch):
    monkeypatch.setattr(module.BackgroundScheduler, 'start', lambda self: None)
    plugin = module.NodeSeekSign()
    plugin.init_plugin({'enabled':False, 'cron':'30 9 * * *'})
    assert not plugin._scheduler.get_jobs()
    plugin.init_plugin({'nodeseeksign_enabled':'true', 'onlyonce':True, 'random_choice':False, 'cookie':'fixture-cookie', 'cron':'30 9 * * *'})
    assert len(plugin._scheduler.get_jobs()) == 2
    assert plugin.saved_config['onlyonce'] is False
    assert plugin.saved_config['enabled'] is True
    assert plugin.saved_config['random_sign'] is False
    assert plugin.saved_config['cookie'] == 'fixture-cookie'
    assert plugin.get_service() == []
    assert plugin.get_form()[0]
    assert plugin.get_page()
    plugin.stop_service()
