"""Tests for half-degree step and demand (max power) control on BRP069/BRP072C."""

from unittest.mock import MagicMock

from aiohttp import ClientSession
import pytest
import pytest_asyncio

from pydaikin.daikin_base import Appliance
from pydaikin.daikin_brp069 import DaikinBRP069
from pydaikin.daikin_brp072c import DaikinBRP072C


@pytest_asyncio.fixture
async def client_session():
    session = ClientSession()
    yield session
    await session.close()


def test_target_temperature_step_brp069():
    """BRP069/BRP072C accept half-degree setpoints."""
    session = MagicMock()
    assert DaikinBRP069('127.0.0.1', session=session).target_temperature_step == 0.5
    assert (
        DaikinBRP072C('127.0.0.1', session=session, key='k').target_temperature_step
        == 0.5
    )


def test_target_temperature_step_base_default():
    """The base appliance defaults to whole-degree steps."""
    assert Appliance('127.0.0.1', session=MagicMock()).target_temperature_step == 1.0


@pytest.mark.parametrize(
    'values, expected',
    [
        ({'dmnd': '1'}, True),
        ({'dmnd': '0'}, False),
        ({}, False),
    ],
)
def test_support_demand_control(values, expected):
    """support_demand_control is derived from the model_info 'dmnd' flag."""
    device = DaikinBRP069('127.0.0.1', session=MagicMock())
    device.values.update(values)
    assert device.support_demand_control is expected


def test_support_demand_control_base_default():
    """The base appliance does not support demand control."""
    assert Appliance('127.0.0.1', session=MagicMock()).support_demand_control is False


@pytest.mark.parametrize(
    'demand, expected',
    [
        ({}, None),
        ({'en_demand': '0', 'max_pow': '40'}, 100.0),
        ({'en_demand': '1', 'max_pow': '40'}, 40.0),
        ({'en_demand': '1', 'max_pow': '100'}, 100.0),
        ({'en_demand': '1'}, None),
        ({'en_demand': '1', 'max_pow': 'invalid'}, None),
    ],
)
def test_max_power(demand, expected):
    """max_power reflects the cached demand state; 100 means no cap."""
    device = DaikinBRP069('127.0.0.1', session=MagicMock())
    device._demand = demand
    assert device.max_power == expected


@pytest.mark.asyncio
async def test_set_max_power(aresponses, client_session):
    """set_max_power reads current demand config, overrides it, and re-sends it."""
    aresponses.add(
        path_pattern='/aircon/get_demand_control',
        method_pattern='GET',
        response=aresponses.Response(
            status=200,
            text=(
                'ret=OK,type=1,en_demand=0,mode=0,max_pow=50,scdl_per_day=4,'
                'moc=0,tuc=0,wec=0,thc=0,frc=0,sac=0,suc=0'
            ),
        ),
    )
    aresponses.add(
        path_pattern='/aircon/set_demand_control',
        method_pattern='GET',
        response=aresponses.Response(status=200, text='ret=OK'),
    )

    device = DaikinBRP069('ip', session=client_session)
    # Simulate a live HVAC mode in the shared values to prove it survives the call.
    device.values.update({'dmnd': '1', 'mode': '3'})

    await device.set_max_power(40)

    # The demand 'mode'/'type' keys must not clobber the HVAC 'mode' in self.values.
    assert device.values['mode'] == '3'
    assert device.max_power == 40.0

    set_request = aresponses.history[-1].request
    assert set_request.path == '/aircon/set_demand_control'
    assert set_request.query['max_pow'] == '40'
    assert set_request.query['en_demand'] == '1'
    assert set_request.query['type'] == '1'
    # Schedule/day flags read from the current config are preserved.
    assert set_request.query['sac'] == '0'
    # The set endpoint expects an empty 'lpw' and does not take 'scdl_per_day'.
    assert set_request.query['lpw'] == ''
    assert 'scdl_per_day' not in set_request.query

    aresponses.assert_all_requests_matched()


@pytest.mark.asyncio
async def test_set_max_power_100_disables_cap(aresponses, client_session):
    """Setting 100% disables demand control (en_demand=0)."""
    aresponses.add(
        path_pattern='/aircon/get_demand_control',
        method_pattern='GET',
        response=aresponses.Response(
            status=200,
            text=(
                'ret=OK,type=1,en_demand=1,mode=0,max_pow=40,scdl_per_day=4,'
                'moc=0,tuc=0,wec=0,thc=0,frc=0,sac=0,suc=0'
            ),
        ),
    )
    aresponses.add(
        path_pattern='/aircon/set_demand_control',
        method_pattern='GET',
        response=aresponses.Response(status=200, text='ret=OK'),
    )

    device = DaikinBRP069('ip', session=client_session)
    device.values.update({'dmnd': '1'})

    await device.set_max_power(100)

    set_request = aresponses.history[-1].request
    assert set_request.query['en_demand'] == '0'
    assert set_request.query['max_pow'] == '100'
    assert device.max_power == 100.0

    aresponses.assert_all_requests_matched()
