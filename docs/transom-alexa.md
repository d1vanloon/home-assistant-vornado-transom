# Transom on Alexa

`tests/fixtures/transom_alexa.json` is one Alexa Nexus reading of a Vornado Transom. Names, serial numbers, and endpoint ids in that file are placeholders. `A1AZ0VKGUB06NH` is the product type Alexa returns as the model. It identifies the product, not one unit.

## Endpoint

The display category is `FAN`. Enablement is `ENABLED`. Manufacturer text is Vornado. The legacy appliance type is `FAN`. `friendlyDescription` is `Vornado Transom`. `connectedVia` is `Wi-Fi`.

`features` has eight entries. `power` is the only entry with operations, and those operations are `turnOn` and `turnOff`. Every other entry sets `operations` to null. No `features` entry carries an instance. Instance strings are on `legacyAppliance.capabilities`, and again on the polled feature state.

## Capabilities

`Alexa.PowerController` has no instance. The supported property is `powerState`. `readOnly` is false.

`Alexa.ModeController` instance `1` is named Fan Speed and Speed. It is ordered and writable. The mode values are strings.

- `1` is named Low, Minimum, Min, and Level One.
- `2` is named Medium and Level Two.
- `3` is named High and Level Three.
- `4` is named Turbo, Level Four, Maximum, and Max.

`Alexa.ModeController` instance `2` is named Direction and Wind. It is ordered and writable.

- `1` is named Exhaust and Outlet.
- `2` is named Direct and inlet.

`Alexa.RangeController` instance `3` is named Set Temperature and Temperature. It is writable. The range is 60 to 90 degrees, and the precision is 1. The presets are 60 Low, 70 Medium, 80 High, and 90 Maximum.

`Alexa.RangeController` instance `5` is named Air Temperature. `readOnly` is true. The range is 0 to 100 degrees, and the precision is 1.

`Alexa.ToggleController` instance `4` is named Auto, Automatic, and Autoclimate. The supported property is `toggleState`. `readOnly` is false.

`Alexa.EndpointHealth` has no instance. The supported property is `connectivity`. The payload sets `readOnly` to false.

## Fan state while off

`fan_state.features` is the parsed state from the same read. The client stores power, mode, and range only, so the toggle value from Alexa is not in the file.

- `power` is `OFF`.
- Mode instance `1` is `0`.
- Mode instance `2` is `0`.
- Range instance `3` is `0`.
- Range instance `5` is `72`.

`0` is not a supported mode value. The fan was off. `0` is also below the set-temperature range of 60 to 90.

## Bindings

Discovery keeps this endpoint. Speed uses ModeController instance `1`. The preset labels are the first text of each mode, Low, Medium, High, and Turbo. The Alexa mode strings are `1`, `2`, `3`, and `4`.

Direction uses ModeController instance `2`. Direct is Alexa mode `2`. Exhaust is Alexa mode `1`. Forward sends Direct. Reverse sends Exhaust.

Air temperature uses RangeController instance `5`. The sensor reports degrees Fahrenheit.

Set temperature and the autoclimate toggle are on the device. Discovery does not bind them.
