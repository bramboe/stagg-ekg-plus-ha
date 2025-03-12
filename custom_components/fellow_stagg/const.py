# """Constants for Fellow Stagg EKG+ integration."""
# DOMAIN = "fellow_stagg"
#
#
# # Main services
# MAIN_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"
# CONTROL_CHAR_UUID = "2291c4b5-5d7f-4477-a88b-b266edb97142"
#
# # BLE UUIDs for the Fellow Stagg kettle services and characteristics
# SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Main control service
# CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"    # Primary characteristic for control
#
# # Additional characteristics for different functions
# TEMP_CHAR_UUID = "021AFF51-0382-4AEA-BFF4-6B3F1C5ADFB4"
# STATUS_CHAR_UUID = "021AFF52-0382-4AEA-BFF4-6B3F1C5ADFB4"
# SETTINGS_CHAR_UUID = "021AFF53-0382-4AEA-BFF4-6B3F1C5ADFB4"
# INFO_CHAR_UUID = "021AFF54-0382-4AEA-BFF4-6B3F1C5ADFB4"
#
#
# # The magic init sequence for the EKG-2d-25-b0 kettle
# INIT_SEQUENCE = bytes.fromhex("455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744")
#


DOMAIN = "fellow_stagg"

# BLE UUIDs for the Fellow Stagg kettle’s “Serial Port Service”
SERVICE_UUID = "021A9004-0382-4AEA-BFF4-6B3F1C5ADFB4"  # Main control service
CHAR_UUID = "021AFF50-0382-4AEA-BFF4-6B3F1C5ADFB4"    # Primary characteristic for control

# The magic init sequence (in hex) used to authenticate with the kettle:
# ef dd 0b 30 31 32 33 34 35 36 37 38 39 30 31 32 33 34 9a 6d
INIT_SEQUENCE = bytes.fromhex("455350100125A2012220889794D1273C492FD635D0DD20AD3F972C0CE3B95D4FB4B5B24D2EAD51DD4EABE3ED637744")
