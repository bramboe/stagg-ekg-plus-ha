DOMAIN = "fellow_stagg"

# Primary service UUID for Fellow Stagg kettle
SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"

# Secondary service UUID (control service)
CONTROL_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"

# Main service characteristics
CHAR_MAIN_UUID = "021aff50-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_TEMP_UUID = "021aff51-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_STATUS_UUID = "021aff52-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_SETTINGS_UUID = "021aff53-0382-4aea-bff4-6b3f1c5adfb4"
CHAR_INFO_UUID = "021aff54-0382-4aea-bff4-6b3f1c5adfb4"

# Control service characteristics
CHAR_CONTROL_UUID = "2291c4b5-5d7f-4477-a88b-b266edb97142"  # For notifications
CHAR_WRITE_UUID = "2291c4b7-5d7f-4477-a88b-b266edb97142"  # For writing commands

# Based on sniffer data, we may not need a magic init sequence
# but keeping it as a placeholder for now
INIT_SEQUENCE = None  # Will determine if needed after further testing
