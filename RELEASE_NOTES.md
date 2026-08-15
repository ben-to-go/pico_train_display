# Release Notes

## Unreleased

- The board is laid out like a real platform indicator.

  Four rows: the next train, the stations it calls at scrolling along the
  middle, the train after it, and the clock alone on the bottom row. The
  rotating third row is numbered 2nd or 3rd; the next train needs no telling.

  Every row is the same nine pixels, as on the real thing. Text uses seven
  of them and leaves two for descenders; the clock, which has none, fills all
  nine. That is the only reason it reads as larger. The bold font is gone and
  the tall one is now nine pixels of digits and a colon.

  The rows are spread down the screen with the gap above the first matching
  the gap below the clock. See `docs/display-format.md`.

  Once the last train has gone the board says "No more departures today"
  rather than welcoming you to the station.

- The board survives the API being down.

  A real weekday morning at Stoke Mandeville is baked into the firmware and
  shown if the first fetch fails, so the display has something on it rather
  than nothing. The stations the first train calls at are baked in beside it,
  since fetching those is a second request the device cannot make either. A successful fetch replaces them and it never goes back. A two
  pixel dot in the bottom right corner is the only sign that what is on
  screen is not current.

  The device no longer resets when updates fail, and the API timeout is five
  seconds rather than ten.

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
