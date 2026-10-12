MIN_DENSITY = 0.50  # keep channels present in >= 50% of rows over the full record
WINDOW_MIN_DENSITY = 0.90  # ...or near-complete within their own coverage window
WINDOW_MIN_SPAN = 0.15  # ...if that window spans >= 15% of the record

MIX_CHANNEL = "temperature_DRI_MIX_CL_DRI_MIX_S"  # cold-mask stage temperatures
PT2_CHANNEL = "temperature_DRI_PT2_S"
