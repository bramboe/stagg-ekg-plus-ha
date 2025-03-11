DOMAIN = "fellow_stagg"

# Primary service UUID for Fellow Stagg kettle
SERVICE_UUID = "021a9004-0382-4aea-bff4-6b3f1c5adfb4"

# Secondary service UUID (control service)
CONTROL_SERVICE_UUID = "7aebf330-6cb1-46e4-b23b-7cc2262c605e"

# Control service characteristic for notifications
CHAR_CONTROL_UUID = "2291c4b5-5d7f-4477-a88b-b266edb97142"

# Control service characteristic for writing commands
CHAR_WRITE_UUID = "2291c4b7-5d7f-4477-a88b-b266edb97142"

# We may not need a magic init sequence based on the logs
INIT_SEQUENCE = None
