# Fellow Stagg Heating Graph Card

## Add the card to your dashboard

1. **Refresh the frontend**  
   After installing or updating the integration, do a **hard refresh**:  
   - Windows/Linux: `Ctrl + Shift + R`  
   - Mac: `Cmd + Shift + R`

2. **Add the card**
   - Go to your **Dashboard** → **Add card**.
   - Choose **Add manually** (or **Custom**).
   - Use this configuration (replace `YOUR_ENTRY_ID` with your Fellow Stagg config entry id from **Settings → Devices & Services → Fellow Stagg** → click your device; the entry id is in the URL):

   ```yaml
   type: custom:fellow-stagg-heating-graph
   entry_id: YOUR_ENTRY_ID
   ```

3. **If the card type does not appear in the list**
   - Go to **Settings → Dashboards → Resources**.
   - Click **Add resource**.
   - **URL:** `/fellow_stagg/fellow_stagg_heating_graph.js`
   - **Type:** JavaScript Module
   - Save, then add the card again as in step 2.

4. **Use the graph**
   - Turn on the **Live Heating Graph** switch for your kettle (so data is collected every second).
   - The card will show current temp, target temp, and heater effort.
