"""Unit tests for the Fellow Stagg HTTP CLI parsers.

Sample bodies are based on live CLI output captured in docs/CLI_TESTING.md.
"""
from kettle_http import KettleHttpClient, _first_not_none, other_ota_slot

# Live-captured style bodies
STATE_BODY = (
    "scrname=wnd value=0 mode=S_Heat tempr=37.82 C temprT=40 C "
    "clock=22:21 units=1 nw=0"
)
SETTINGS_BODY = (
    "clockmode=1 hold=15 schedon=1 schtime=0:0 schtempr=176 offset_temp=-66879 "
    "bricky=0 Repeat_sched=0 boil=1 altitude=100 m language=0 chime=0"
)


class TestParseBoil:
    def test_boil_on(self):
        # Regression: the old regex (r"\boil") could never match "boil=1"
        assert KettleHttpClient._parse_boil("boil=1") is True

    def test_boil_off(self):
        assert KettleHttpClient._parse_boil("boil = 0") is False

    def test_boil_missing(self):
        assert KettleHttpClient._parse_boil("clockmode=1 hold=15") is None

    def test_boil_from_settings_body(self):
        assert KettleHttpClient._parse_boil(SETTINGS_BODY) is True


class TestParseTemps:
    def test_current_temp_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_temp(STATE_BODY)
        assert temp == 37.82
        assert unit == "C"

    def test_target_temp_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_target_temp(STATE_BODY)
        assert temp == 40.0
        assert unit == "C"

    def test_fahrenheit_converted_to_celsius(self):
        client = KettleHttpClient("http://k")
        temp, unit = client._parse_temp("tempr=212 F")
        assert unit == "F"
        assert round(temp, 1) == 100.0

    def test_nan_returns_none(self):
        client = KettleHttpClient("http://k")
        assert client._parse_temp("tempr=nan") == (None, None)


class TestParseMode:
    def test_simple_mode(self):
        assert KettleHttpClient._parse_mode(STATE_BODY) == "S_HEAT"

    def test_mode_with_timer_suffix(self):
        assert KettleHttpClient._parse_mode("mode=S_Heat+timer") == "S_HEAT+TIMER"

    def test_power(self):
        assert KettleHttpClient._parse_power("S_HEAT") is True
        assert KettleHttpClient._parse_power("S_OFF") is False
        assert KettleHttpClient._parse_power(None) is None

    def test_hold(self):
        assert KettleHttpClient._parse_hold("S_HOLD") is True
        assert KettleHttpClient._parse_hold("S_HEAT") is False
        assert KettleHttpClient._parse_hold("S_HOLD+timer") is True


class TestParseClock:
    def test_clock(self):
        assert KettleHttpClient._parse_clock(STATE_BODY) == "22:21"

    def test_clock_pads_and_wraps(self):
        assert KettleHttpClient._parse_clock("clock=7:5") == "07:05"

    def test_clock_mode(self):
        assert KettleHttpClient._parse_clock_mode(SETTINGS_BODY) == 1
        assert KettleHttpClient._parse_clock_mode("clockmode=9") is None


class TestParseSchedule:
    def test_schedule_time_colon_format(self):
        assert KettleHttpClient._parse_schedule_time("schtime=7:30") == {
            "hour": 7,
            "minute": 30,
        }

    def test_schedule_time_encoded(self):
        # (7 << 8) | 30 = 1822
        assert KettleHttpClient._parse_schedule_time("schtime=1822") == {
            "hour": 7,
            "minute": 30,
        }

    def test_schedule_temp_f_to_c(self):
        client = KettleHttpClient("http://k")
        # 176 F = 80 C
        assert round(client._parse_schedule_temp(SETTINGS_BODY), 1) == 80.0

    def test_schedule_temp_out_of_range(self):
        client = KettleHttpClient("http://k")
        assert client._parse_schedule_temp("schtempr=0") is None

    def test_schedon(self):
        assert KettleHttpClient._parse_schedon_value(SETTINGS_BODY) == 1
        assert KettleHttpClient._parse_schedule_enabled("schedon=0") is False
        assert KettleHttpClient._parse_schedule_repeat(SETTINGS_BODY) == 0


class TestParseTimers:
    def test_countdown_pre_start(self):
        minutes, phase = KettleHttpClient._parse_countdown("mode=S_Heat value=3")
        assert minutes == 3
        assert phase == "pre_start"

    def test_countdown_prefers_time_over_value(self):
        minutes, phase = KettleHttpClient._parse_countdown(
            "mode=S_Hold value=0 time 1:10"
        )
        assert minutes == 1
        assert phase == "hold"

    def test_countdown_off_when_standby(self):
        assert KettleHttpClient._parse_countdown("mode=S_Off value=3") == (None, None)

    def test_timer_time(self):
        display, total = KettleHttpClient._parse_timer_time(
            "mode=S_Heat+timer Main: time 3:45 temp 90"
        )
        assert display == "3:45"
        assert total == 225

    def test_timer_none_when_idle(self):
        assert KettleHttpClient._parse_timer_time("mode=S_Off") == (None, None)


class TestParseFlags:
    def test_units_flag(self):
        assert KettleHttpClient._parse_units_flag("units=1") == "C"
        assert KettleHttpClient._parse_units_flag("units=0") == "F"
        assert KettleHttpClient._parse_units_flag("") is None

    def test_lifted_only_on_nan(self):
        assert KettleHttpClient._parse_lifted("tempr=nan") is True
        assert KettleHttpClient._parse_lifted(STATE_BODY) is False

    def test_no_water(self):
        assert KettleHttpClient._parse_no_water("nw=1") is True
        assert KettleHttpClient._parse_no_water(STATE_BODY) is False

    def test_hold_setting(self):
        assert KettleHttpClient._parse_hold_setting(SETTINGS_BODY) == 15

    def test_fwinfo(self):
        body = "Current version: 1.2.5CL cli\nota_1 1.2.5CL"
        assert KettleHttpClient._parse_fwinfo(body) == "1.2.5CL"


class TestNewParsers:
    def test_altitude_meters(self):
        assert KettleHttpClient._parse_altitude_m(SETTINGS_BODY) == 100.0
        assert KettleHttpClient._parse_altitude_m("altitude=0 m") == 0.0
        assert KettleHttpClient._parse_altitude_m("clockmode=1") is None

    def test_altitude_feet_converted_to_meters(self):
        # Regression: kettle may report feet; entity works in meters
        # 1000 ft = 304.8 m
        assert KettleHttpClient._parse_altitude_m("altitude=1000 ft") == 304.8

    def test_language(self):
        assert KettleHttpClient._parse_language(SETTINGS_BODY) == 0
        assert KettleHttpClient._parse_language("language=6") == 6
        assert KettleHttpClient._parse_language("") is None

    def test_chime(self):
        assert KettleHttpClient._parse_chime("chime=0") is False
        assert KettleHttpClient._parse_chime("chime=1") is True
        assert KettleHttpClient._parse_chime("chime = 3") is True
        assert KettleHttpClient._parse_chime("clockmode=1") is None

    def test_boil_point(self):
        assert KettleHttpClient._parse_boil_point("temprB=100.000000 C") == 100.0
        assert KettleHttpClient._parse_boil_point("temprB=93.4 C") == 93.4
        assert KettleHttpClient._parse_boil_point("temprB=nan") is None
        assert KettleHttpClient._parse_boil_point("mode=S_Off") is None

    def test_ketl_flags(self):
        flags = KettleHttpClient._parse_ketl_flags(
            "ketl= ho 0 wd 0 nw 1 ipb 0 bf 0 tr 0"
        )
        assert flags == {"ho": 0, "wd": 0, "nw": 1, "ipb": 0, "bf": 0, "tr": 0}
        assert KettleHttpClient._parse_ketl_flags("mode=S_Off") is None


class TestHelpers:
    def test_first_not_none_prefers_zero_over_fallback(self):
        # Regression: "or" chains let 0 from prtsettings fall through to stale state
        assert _first_not_none(0, 15) == 0
        assert _first_not_none(None, 15) == 15
        assert _first_not_none(None, None) is None
        assert _first_not_none(False, True) is False

    def test_encode_cli_command(self):
        assert KettleHttpClient._encode_cli_command("setstate S_Heat") == "setstate+S_Heat"

    def test_base_url_normalization(self):
        client = KettleHttpClient("192.168.1.86")
        assert client._cli_url == "http://192.168.1.86/cli"
        client = KettleHttpClient("http://192.168.1.86/")
        assert client._cli_url == "http://192.168.1.86/cli"

    def test_screen_name(self):
        assert KettleHttpClient._parse_screen_name(STATE_BODY) == "wnd"

    def test_root_url(self):
        assert KettleHttpClient("http://192.168.1.86/")._root_url == "http://192.168.1.86/"
        assert KettleHttpClient("http://192.168.1.86/cli")._root_url == "http://192.168.1.86/"


# Root page of a kettle running 1.1.76SSP with 1.2.24 in the other slot (live capture, Oct 2026)
ROOT_PAGE_OLD = (
    "<h1>EKG</h1>Current version: 1.1.76SSP CLI<br>Build time: 14:03:33<br>"
    "Build date: May  9 2024<br>Boot partition: ota_0<br>Running partition: ota_0<br>"
    "Last invalid partition: <br>"
    "partition 'factory' at 0x10000 size 0x200000 encr 0 state ?? fw version 1.1.14SSB<br>"
    "partition 'ota_0' at 0x210000 size 0x200000 encr 0 state valid fw version 1.1.76SSP<br>"
    "partition 'ota_1' at 0x410000 size 0x200000 encr 0 state valid fw version 1.2.24<br>\n\n"
    '            <form action="cli" method="GET">\n'
    '            <label for="x">CLI Command:</label><br>\n'
    '            <input type="text" id="cli" name="cmd"><br>\n'
    "            </form>"
)
# Same page on 1.2.24 as quoted in issue #5 (line breaks, padded columns)
ROOT_PAGE_NEW = (
    "<h1>EKG</h1>Current version: 1.2.24 CLI\nBuild time: 20:50:09\nBuild date: Sep 30 2026\n"
    "Boot partition: ota_0\nRunning partition: ota_0\nLast invalid partition:\n"
    "partition 'factory' at 0x10000 size 0x200000 encr 0 state ?? fw version 1.1.14SSB\n"
    "partition 'ota_0'   at 0x210000 size 0x200000 encr 0 state valid fw version 1.2.24\n"
    "partition 'ota_1'   at 0x410000 size 0x200000 encr 0 state valid fw version 1.1.76SSP\n"
)
CLI_FORM_ONLY = (
    '\n            <form action="cli" method="GET">\n'
    '            <label for="x">CLI Command:</label><br>\n'
    '            <input type="text" id="cli" name="cmd"><br>\n'
    "            </form>\n            "
)


class TestFirmwarePage:
    def test_parse_old_firmware_page(self):
        fw = KettleHttpClient._parse_partitions(ROOT_PAGE_OLD)
        assert fw["current_version"] == "1.1.76SSP"
        assert fw["running"] == "ota_0"
        assert fw["boot"] == "ota_0"
        assert fw["slots"]["ota_0"] == {"state": "valid", "version": "1.1.76SSP"}
        assert fw["slots"]["ota_1"] == {"state": "valid", "version": "1.2.24"}
        assert fw["slots"]["factory"] == {"state": "??", "version": "1.1.14SSB"}

    def test_parse_new_firmware_page(self):
        fw = KettleHttpClient._parse_partitions(ROOT_PAGE_NEW)
        assert fw["current_version"] == "1.2.24"
        assert fw["running"] == "ota_0"
        assert fw["slots"]["ota_1"]["version"] == "1.1.76SSP"

    def test_parse_unrelated_page(self):
        assert KettleHttpClient._parse_partitions("This URI does not exist") is None
        assert KettleHttpClient._parse_partitions("") is None

    def test_other_ota_slot(self):
        assert other_ota_slot(KettleHttpClient._parse_partitions(ROOT_PAGE_OLD)) == "ota_1"
        assert other_ota_slot(KettleHttpClient._parse_partitions(ROOT_PAGE_NEW)) == "ota_1"
        assert other_ota_slot({"running": "ota_1", "slots": {"factory": {}, "ota_0": {}, "ota_1": {}}}) == "ota_0"
        assert other_ota_slot({"running": "ota_0", "slots": {"ota_0": {}}}) is None
        assert other_ota_slot(None) is None


class TestCliMuted:
    def test_form_only_is_muted(self):
        # Firmware 1.2.24: every command answers with just the input form
        assert KettleHttpClient._cli_output_missing(CLI_FORM_ONLY) is True

    def test_form_with_output_is_not_muted(self):
        body = CLI_FORM_ONLY + "I (25071) Cli: cmd len 5: 'state'\nmode=S_Off\ntempr=nan C\n"
        assert KettleHttpClient._cli_output_missing(body) is False

    def test_empty_or_plain_body_is_not_muted(self):
        assert KettleHttpClient._cli_output_missing("") is False
        assert KettleHttpClient._cli_output_missing(STATE_BODY) is False
