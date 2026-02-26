Deploy the intercom hub add-on to the Home Assistant server and rebuild it.

Steps:
1. Copy updated files to HA server at 10.0.0.8:
   ```
   scp intercom_hub/intercom_hub.py root@10.0.0.8:/addons/intercom_hub/
   scp intercom_hub/config.yaml root@10.0.0.8:/addons/intercom_hub/
   scp intercom_hub/www/index.html root@10.0.0.8:/addons/intercom_hub/www/
   scp intercom_hub/www/ptt-v7.js root@10.0.0.8:/addons/intercom_hub/www/
   ```
2. Rebuild the add-on:
   ```
   ssh root@10.0.0.8 "ha apps rebuild local_intercom_hub"
   ```
3. Wait ~5 seconds, then tail logs to confirm startup:
   ```
   ssh root@10.0.0.8 "ha apps logs local_intercom_hub --lines 30"
   ```
4. Report the hub version and any errors from the logs.

Working directory: /home/user/Projects/assistantlisteners
