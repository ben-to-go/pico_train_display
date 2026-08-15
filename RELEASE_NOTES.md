# Release Notes

## Unreleased

- Moved to the next generation Realtime Trains API at `https://data.rtt.io`.

  Sign up at [api-portal.rtt.io](https://api-portal.rtt.io/) and put the token
  in `rtt.token`; `rtt.username` and `rtt.password` are gone. The endpoint
  default has changed, so existing `config.json` files need updating.

- Removed "slow stations" and the fast train icon.

  The board now shows time, destination and status, and nothing else. The
  `slow_station` setting is gone and must be removed from `config.json`.

- Removed the optional web server in `server/`.

  It existed to add calling-at stations for the fast train icon and to trim
  the old API's responses, and neither is needed now.

## v1.1.0

- Added optional supoprt for "slow stations".

  Trains that don't stop at a slow station are marked as "fast", showing a small
  icon.

  NB: To use this feature you need to run a the custom web server below to get
  the calling stations.

- Implemented custom web server.

  This is so that we can reduce the JSON response message from RTT to only the
  fields we're interested in, and to add additional calling-at stations that are
  too memory-intensive to run on-device.

- Fixed incorrect destination station for cancelled trains.

## v1.0.0

- Initial release
