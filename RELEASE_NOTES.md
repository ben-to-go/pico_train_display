# Release Notes

## v2.0.0

Rebuilt around the hardware it actually runs on, and around the API not always
being there.

- Drives the panel over 8080 8-bit parallel instead of SPI.
- Runs on the Pico 2 W, and only the Pico 2 W.
- Moved to the next generation Realtime Trains API at `https://data.rtt.io`.
- Laid out like a real platform indicator, measured off a photograph of one.
- Keeps showing departures when the wifi, the clock or the API give out.
- Polls inside the API's rate limit, and backs off when it is refused.
- Gained a desktop simulator, a one command firmware build and a build guide.
- Lost the proxy server, the e-Paper display and the fast train icon.

### Breaking

Nothing from v1.1.0 carries over untouched.

- **Rewire it.** SPI is gone. See
  [docs/build-your-own.md](docs/build-your-own.md).
- **A Pico W will not run this.** The release carries one image, for the
  Pico 2 W.
- **Start `config.json` again.** `rtt.endpoint` and `rtt.token` replace
  `rtt.username` and `rtt.password`, and `display.type` and `slow_station` are
  gone. Deleting the file and using the setup screen is the quickest way.

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
