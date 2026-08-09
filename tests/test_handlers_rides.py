from chain_health.bot import texts as bot_texts
from chain_health.bot.callbacks import RideAction, RideCB
from chain_health.services.rides import RideService
from tests.helpers.factories import onboard


async def _first_ride_id(container, user_id: int = 1) -> int:
    async with container() as rc:
        rides = await rc.get(RideService)
        return (await rides.recent_rides(user_id))[0].id


async def test_editing_a_ride_rejects_an_amount_above_max_distance_km(harness, container):
    """Same MAX_DISTANCE_KM bound as adding a ride — editing must not be a
    back door to a distance that permanently corrupts cycle_km/total_km.
    """
    await onboard(harness)
    await harness.send("42", user_id=1)
    ride_id = await _first_ride_id(container)
    harness.session.clear()

    await harness.click(RideCB(action=RideAction.EDIT, ride_id=ride_id).pack(), user_id=1)
    await harness.send("9999999", user_id=1)

    assert harness.session.sent_texts()[-1] == bot_texts.ASK_NUMBER_RETRY
    async with container() as rc:
        rides = await rc.get(RideService)
        ride = await rides.require_ride(1, ride_id)
        assert ride.distance_km == 42


async def test_editing_a_ride_accepts_an_amount_within_max_distance_km(harness, container):
    await onboard(harness)
    await harness.send("42", user_id=1)
    ride_id = await _first_ride_id(container)
    harness.session.clear()

    await harness.click(RideCB(action=RideAction.EDIT, ride_id=ride_id).pack(), user_id=1)
    await harness.send("100", user_id=1)

    async with container() as rc:
        rides = await rc.get(RideService)
        ride = await rides.require_ride(1, ride_id)
        assert ride.distance_km == 100
