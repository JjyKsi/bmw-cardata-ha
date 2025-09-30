<p align="center">
  <img src="logo.png" alt="BimmerData Streamline logo" width="240" />
</p>

# BimmerData Streamline (BMW CarData for Home Assistant)

## This is experimental. 
Wait for the core integration to get fixed if you want stable experience but if you want to help, keep reporting the bugs and I'll take a look! :) The Beta branch is used as a day to day development branch and can contain completely broken stuff. The main branch is updated when I feel that it works well enough and has something new. However, the integration currently lacks proper testing and I also need to keep my own automations running so not everything is tested on every release and there's a possibility that something works on my instance, since I already had something installed. Create an issue if you have problems when making a clean install. 


## Release Notes: 
#### 30.9.2025
### CarData API implemented
In addition to the stream, we now also poll the API every 40 minutes. There is still some space to make this higher resolution and I will also plan to make it so, that we wont poll at the same time as stream is online to save some quota for later.

### Better names to entities and sensors
Vehicles should now be named after their actual model. You can still see the VIN briefly in some situations
Sensor friendly names are also revamped to be CarModel - SensorName. Sensor names are AI generated from the BMW catalogue. Please report or create a PR if you see something stupid. The sensor names are available in custom_components/cardata/descriptor_titles.py

### More stable stream implementation
Stream shouldn't reconnect every 70 seconds anymore. However, reconnection every 45 minutes is needed since BMW tokens are pretty shortlived. 

### Configure button actions
On the integration main page, there is now "Configure" button. You can use it to:
- Refresh authentication tokens (will reload integration, might also need HA restart in some problem cases)
- Start device authorization again (redo the whole auth flow. Not tested yet but should work ™️)

And manual API calls, these should be automatically called when needed, but if it seems that your device names aren't being updated, it might be worth it to run these manually. 
- Initiate Vehicles API call (Fetch all Vehicle VINS on your account and create entities out of them)
- Get Basic Vehicle Information (Fetches vehicle details like model, etc. for all known VINS)
- Get telematics data (Fetches a telematics data from the CarData API. This is a limited hardcoded subset compared to stream. I can add more if needed)

Note that every API call here counts towards your 50/24h quota!


Turn your BMW CarData stream into native Home Assistant entities. This integration subscribes directly to the BMW CarData MQTT stream, keeps the token fresh automatically, and creates sensors/binary sensors for every descriptor that emits data.

**IMPORTANT: I released this to public after verifying that it works on my automations, so the testing time has been quite low so far. If you're running any critical automations, please don't use this plugin yet.**

> **Note:** This entire plugin was generated with the assistance of AI to quickly solve issues with the legacy implementation. The code is intentionally open—to-modify, fork, or build a new integration from it. PRs are welcome unless otherwise noted in the future.

> **Tested Environment:** The integration has only been verified on my own outdated Home Assistant instance (2024.12.5). Newer releases might require adjustments.

> **Heads-up:** I've tested this on 2022 i4 and 2016 i3. Both show up entities, i4 sends them instantly after locking/closing the car remotely using MyBMW app. i3 seems to send the data when it wants to. So far after reinstalling the plugin, I haven't seen anything for an hour, but received data multiple times earlier. So be patient, maybe go and drive around or something to trigger the data transfer :) 

## BMW Portal Setup (DON'T SKIP, DO THIS FIRST)

The CarData web portal isn’t available everywhere (e.g., it’s disabled in Finland). You can still enable streaming by logging into https://www.bmw.de/de-de/mybmw/vehicle-overview and following these steps:

1. Select the vehicle you want to stream.
2. Choose **BMW CarData**.
3. Generate a client ID as described here: https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Technical-registration_Step-1
4. Subscribe the client to both scopes: `cardata:api:read` and `cardata:streaming:read` and click authorize.
4.1. Note, BMW portal seems to have some problems with scope selection. If you see an error on the top of the page, reload it, select one scope and wait for +30 seconds, then select the another one and wait agin.
5. Scroll to the **Data Selection** section (`Datenauswahl ändern`) and load all descriptors (keep clicking “Load more”).
6. Check every descriptor you want to stream. To automate this, open the browser console and run:
   - If you want the "Extrapolated SOC" helper sensor to work, make sure your telematics container includes the descriptors `vehicle.drivetrain.batteryManagement.header`, `vehicle.drivetrain.batteryManagement.maxEnergy`, `vehicle.powertrain.electric.battery.charging.power`, and `vehicle.drivetrain.electricEngine.charging.status`. Those fields let the integration reset the extrapolated state of charge and calculate the charging slope between stream updates.

```js
(() => {
  const labels = document.querySelectorAll('.css-k008qs label.chakra-checkbox');
  let changed = 0;

  labels.forEach(label => {
    const input = label.querySelector('input.chakra-checkbox__input[type="checkbox"]');
    if (!input || input.disabled || input.checked) return;

    label.click();
    if (!input.checked) {
      const ctrl = label.querySelector('.chakra-checkbox__control');
      if (ctrl) ctrl.click();
    }
    if (!input.checked) {
      input.checked = true;
      ['click', 'input', 'change'].forEach(type =>
        input.dispatchEvent(new Event(type, { bubbles: true }))
      );
    }
    if (input.checked) changed++;
  });

  console.log(`Checked ${changed} of ${labels.length} checkboxes.`);
})();
```

7. Save the selection.
8. Repeat for all the cars you want to support
9. Install this integration via HACS.
10. During the Home Assistant config flow, paste the client ID, visit the provided verification URL, enter the code (if asked), and approve. **Do not click Continue/Submit in Home Assistant until the BMW page confirms the approval**; submitting early leaves the flow stuck and requires a restart.
11. Wait for the car to send data—triggering an action via the MyBMW app (lock/unlock doors) usually produces updates immediately.

## Installation (HACS)

1. Add this repo to HACS as a **custom repository** (type: Integration).
2. Install "BimmerData Streamline" from the Custom section.
3. Restart Home Assistant.

## Configuration Flow

1. Go to **Settings → Devices & Services → Add Integration** and pick **BimmerData Streamline**.
2. Enter your CarData **client ID** (created in the BMW portal).
3. The flow displays a `verification_url` and `user_code`. Open the link, enter the code, and approve the device.
4. Once the BMW portal confirms the approval, return to HA and click Submit. If you accidentally submit before finishing the BMW login, the flow will hang until the device-code exchange times out; cancel it and start over after completing the BMW login.
5. If you remove the integration later, you can re-add it with the same client ID—the flow deletes the old entry automatically.

### Reauthorization
If BMW rejects the token (e.g. because the portal revoked it), please use the Configure > Start Device Authorization Again tool

## Entity Naming & Structure

- Each VIN becomes a device in HA (`VIN` pulled from CarData).
- Sensors/binary sensors are auto-created and named from descriptors (e.g. `Cabin Door Row1 Driver Is Open`).
- Additional attributes include the source timestamp.

## Debug Logging
Set `DEBUG_LOG = True` in `custom_components/cardata/const.py` for detailed MQTT/auth logs (enabled by default). To reduce noise, change it to `False` and reload HA.

## Developer Tools Services

Home Assistant's Developer Tools expose helper services for manual API checks:

- `cardata.fetch_telematic_data` fetches the current contents of the configured telematics container for a VIN and logs the raw payload.
- `cardata.fetch_vehicle_mappings` calls `GET /customers/vehicles/mappings` and logs the mapping details (including PRIMARY or SECONDARY status). Only primary mappings return data; some vehicles do not support secondary users, in which case the mapped user is considered the primary one.
- `cardata.fetch_basic_data` calls `GET /customers/vehicles/{vin}/basicData` to retrieve static metadata (model name, series, etc.) for the specified VIN.

## Requirements

- BMW CarData account with streaming access (CarData API + CarData Streaming subscribed in the portal).
- Client ID created in the BMW portal (see "BMW Portal Setup").
- Home Assistant 2024.6+.
- Familiarity with BMW’s CarData documentation: https://bmw-cardata.bmwgroup.com/customer/public/api-documentation/Id-Introduction

## Known Limitations

- Only one BMW stream per GCID: make sure no other clients are connected simultaneously.
- The CarData API is read-only; sending commands remains outside this integration.
- Premature Continue in auth flow: If you hit Continue before authorizing on BMW’s site, the device-code flow gets stuck. Cancel the flow and restart the integration (or Home Assistant) once you’ve completed the BMW login.

## License

This project is released into the public domain. Do whatever you want with it—personal, commercial, derivative works, etc. No attribution required (though appreciated).
