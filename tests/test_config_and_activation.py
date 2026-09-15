from __future__ import annotations

import pytest

from adt_cli import activation, config
from adt_cli.errors import ConfigError
from adt_cli.repository import RepoObject


@pytest.mark.parametrize("bad", ["sap.example.com", "ftp://sap", "", "/relative"])
def test_host_must_be_an_absolute_http_url(bad):
    with pytest.raises(ConfigError):
        config.validate_host(bad)


def test_host_is_normalised():
    assert config.validate_host("https://sap.example.com:44300/") == "https://sap.example.com:44300"


@pytest.mark.parametrize(("raw", "expected"), [("100", "100"), ("1", "001"), (" 110 ", "110")])
def test_client_is_padded(raw, expected):
    assert config.validate_client(raw) == expected


@pytest.mark.parametrize("bad", ["abc", "1000", ""])
def test_client_must_be_digits(bad):
    with pytest.raises(ConfigError):
        config.validate_client(bad)


@pytest.mark.parametrize("bad", ["", "has space", "a" * 65, "semi;colon"])
def test_system_name_is_restricted(bad):
    with pytest.raises(ConfigError):
        config.validate_name(bad)


def test_activation_body_escapes_names():
    body = activation._body([RepoObject('ZCL_"A"&B', "CLAS/OC", "/uri?a=1&b=2")])
    assert "&amp;" in body
    assert '"ZCL_"A"&B"' not in body


def test_activation_parses_errors_and_warnings():
    xml = (
        '<chkl:messages><properties activationExecuted="true"/>'
        '<msg type="E" objDescr="ZCL_A" line="12"><shortText>Syntax error</shortText></msg>'
        '<msg type="W" objDescr="ZCL_A"><shortText>Unused variable</shortText></msg>'
        "</chkl:messages>"
    )
    outcome = activation._parse(xml)
    assert outcome.executed
    assert outcome.errors == ["ZCL_A line 12: Syntax error"]
    assert outcome.warnings == ["ZCL_A: Unused variable"]
    assert not outcome.ok


def test_activation_without_objects_does_not_call_the_server():
    outcome = activation._parse('<properties activationExecuted="true"/>')
    assert outcome.ok
