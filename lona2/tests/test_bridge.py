"""Bridge / stores / monitor / updater / winshape tests.

The bridge must return well-formed dicts (never raise) even with no
window running — on Linux every window method reports honestly that
there is no window, instead of crashing.
"""

from lona2 import updater
from lona2 import winshape
from lona2.bridge import Api
from lona2.monitor import get_device_overview, read_cpu_temp
from lona2.stores import DEFAULT_SETTINGS, get_settings, get_tasks
from lona2.stores import save_settings


def test_settings_defaults_match_brief():
    settings = get_settings()
    assert settings["ai_name"] == "لونا"
    assert settings["user_name"] == "Mohamed"
    assert settings["listen_enabled"] is False  # wake word OFF by default
    assert settings["island_enabled"] is True
    assert settings["r1_ram_free_mb"] == 500
    assert settings["r2_ram_pct"] == 92
    assert settings["r5_cpu_temp_c"] == 85
    assert settings["r6_load_minutes"] == 5
    assert settings["theme"] == "lg"


def test_settings_save_drops_unknown_keys():
    res = save_settings({"ai_name": "ميمو", "not_a_real_key": 1})
    assert res["ok"] is True
    settings = get_settings()
    assert settings["ai_name"] == "ميمو"
    assert "not_a_real_key" not in settings
    assert set(settings) == set(DEFAULT_SETTINGS)


def test_tasks_start_empty():
    assert get_tasks() == []


def test_bridge_status_shape():
    api = Api()
    status = api.get_status()
    assert status["ok"] is True
    assert status["version"] == "2.0.0"
    assert status["wake_word"] == "يا لونا"
    assert status["activated"] is False


def test_bridge_device_overview_shape():
    api = Api()
    res = api.get_device_overview()
    assert res["ok"] is True
    overview = res["overview"]
    assert overview["ram"]["percent"] is not None
    assert overview["ram"]["total_gb"] is not None
    assert overview["cpu"]["percent"] is not None
    assert isinstance(overview["top_processes"], list)


def test_temp_is_honest_on_linux():
    temp = read_cpu_temp()
    assert temp["source"] == "unavailable"
    assert temp["value"] is None
    assert "مش متاحة" in temp["label"]


def test_bridge_model_options_shape():
    api = Api()
    res = api.get_model_options()
    assert "options" in res
    ids = [o["id"] for o in res["options"]]
    assert ids == ["auto", "groq", "gemini", "ollama"]
    for option in res["options"]:
        assert "available" in option and "reason" in option
        if not option["available"]:
            assert option["reason"]


def test_bridge_window_methods_fail_honestly_without_window():
    api = Api()  # app=None: no window exists on Linux
    assert api.window_minimize()["ok"] is False
    assert api.window_toggle_maximize()["ok"] is False
    assert api.island_resize(380, 60)["ok"] is False
    assert api.island_expand()["ok"] is False
    assert api.window_move_to(0, 0)["ok"] is False


def test_bridge_deep_files_path_is_optional():
    api = Api()
    assert api.request_deep_file_access()["ok"] is True
    assert api.request_deep_file_access("/some/path")["ok"] is True
    assert get_settings()["deep_files_allowed"] is True


def test_bridge_invite_bad_codes():
    api = Api()
    assert api.check_invite("12")["result"] == "format"
    assert api.check_invite("000000")["result"] == "invalid"


def test_bridge_whatsapp_is_honest_phase2():
    api = Api()
    status = api.get_whatsapp_status()
    assert status["status"] == "not-built"
    assert api.whatsapp_relink()["ok"] is False


def test_bridge_job_result_unknown_id():
    api = Api()
    res = api.job_result("doesnotexist")
    assert res["done"] is True
    assert res["error"]


def test_bridge_tasks_crud():
    api = Api()
    added = api.add_task("ذاكر CS 303")
    assert added["ok"] is True
    task_id = added["task"]["id"]
    assert api.toggle_task(task_id)["ok"] is True
    assert api.get_tasks()["tasks"][0]["done"] is True
    assert api.delete_task(task_id)["ok"] is True
    assert api.get_tasks()["tasks"] == []
    assert api.add_task("   ")["ok"] is False


def test_bridge_log_error_never_raises():
    api = Api()
    assert api.log_error("ui", "boom")["ok"] is True


def test_winshape_island_radius():
    assert winshape.island_radius(380, 60) == 30
    assert winshape.island_radius(720, 400) == 22
    assert winshape.island_radius(380, 52) == 26


def test_winshape_easing():
    assert winshape.ease_in_out_cubic(0) == 0
    assert winshape.ease_in_out_cubic(1) == 1
    assert winshape.ease_in_out_cubic(0.5) == 0.5
    assert winshape.lerp_rect((0, 0, 100, 100), (10, 10, 200, 200), 0.5) == (5, 5, 150, 150)


def test_updater_install_without_download_fails_cleanly(tmp_path):
    api = Api()
    res = api.install_update()
    assert res["ok"] is False
    # Nothing was deleted: the updates dir must not contain a setup.
    assert not (tmp_path / "lona-test" / "updates" / "LONA-Setup-new.exe").exists()


def test_updater_sha_cache(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"lona" * 1000)
    first = updater.verify_downloaded_sha(str(target))
    target.write_bytes(b"CHANGED" * 1000)
    # The cached hash is reused for the same path (no re-hash storm).
    assert updater.verify_downloaded_sha(str(target)) == first
    assert len(first) == 64

# --- UI/bridge contract: the UI reads flat shapes and short keys -----
# (found by reading index.html/island.html against the bridge).

def test_bridge_get_settings_is_flat_ui_shape():
    api = Api()
    settings = api.get_settings()
    # Flat: the UI reads s.ai_name / s.r1 directly, not s.settings.
    assert settings["ai_name"] == "لونا"
    assert settings["name"] == "لونا"
    assert settings["user_name"] == "Mohamed"
    assert settings["r1"] == 500
    assert settings["r5"] == 85
    assert settings["r6m"] == 5
    assert "settings" not in settings


def test_bridge_save_settings_translates_ui_keys():
    api = Api()
    res = api.save_settings({"r1": 400, "greet": 0, "name": "ميمو"})
    assert res["ok"] is True
    settings = get_settings()
    assert settings["r1_ram_free_mb"] == 400
    assert settings["greet_morning"] == 0
    assert settings["ai_name"] == "ميمو"
    # And the flat read-back shows the same values under UI keys.
    assert api.get_settings()["r1"] == 400
    assert api.get_settings()["name"] == "ميمو"


def test_bridge_save_settings_voice_disambiguated_by_type():
    api = Api()
    api.save_settings({"voice": True})
    assert get_settings()["voice_reply"] is True
    api.save_settings({"voice": "m1"})
    assert get_settings()["voice_choice"] == "m1"
    api.save_settings({"vc": "f2"})
    assert get_settings()["voice_choice"] == "f2"


def test_bridge_get_log_has_text_shape():
    api = Api()
    res = api.get_log()
    assert isinstance(res["log"], str)
    assert isinstance(res["entries"], list)
    assert isinstance(res["lines"], list)


def test_bridge_api_keys_status_is_flat():
    api = Api()
    res = api.get_api_keys_status()
    # The UI reads st[provider] directly (st.groq / st.gemini_chat).
    assert "groq" in res and "gemini_chat" in res
    assert res["groq"] is False
    assert res["status"]["groq"] is False


def test_bridge_model_options_has_choice():
    api = Api()
    res = api.get_model_options()
    assert res["choice"] == "auto"
    assert isinstance(res["options"], list) and res["options"]


def test_bridge_check_updates_pending_shape():
    api = Api()
    res = api.check_updates()
    assert res["pending"] is True
    assert res["id"] == "updates"
    # The UI polls get_update_status(id): it must accept the id and
    # return a dict with a "done" flag (no waiting for the worker).
    status = api.get_update_status("updates")
    assert "done" in status
    assert isinstance(status["done"], bool)
    assert "available" in status


def test_bridge_self_view_has_rows():
    api = Api()
    res = api.get_self_view()
    assert isinstance(res["rows"], list) and res["rows"]
    assert all("label" in row and "value" in row for row in res["rows"])


def test_bridge_morning_brief_has_text():
    api = Api()
    res = api.get_morning_brief()
    assert isinstance(res["brief"], str) and res["brief"]
    assert isinstance(res["items"], list) and res["items"]
