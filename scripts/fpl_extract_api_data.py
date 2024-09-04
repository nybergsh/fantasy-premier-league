# Script extracts data from Fantasy Premier League API and stores as raw csv files in \data\raw\fpl_api folder

# Custom file paths
from fantasy_pl.config import fpl_api_raw_directory
from fantasy_pl.config import fpl_api_bootstrap_static_url
from pathlib import Path

# Other packages
import requests,json
import pandas as pd
import sys

# Ensure that we don't use the same gw variable over and over just because it was added as an argument
run_iter = 0

# Endpoint list to loop through (bootstrap-static)
api_endpoints = ['events','teams','element_types','elements']

while 1==1:
    if len(sys.argv) > 1 and run_iter == 0:
        gameweek = sys.argv[1]  
    else:
        gameweek = input('Which gameweek is this after? [1-38]: ')
    run_iter += 1

    # Check if file already exist
    element_snapshot_path = Path(str(fpl_api_raw_directory)+r'/element_snapshots/elements_gw'+gameweek+'.csv')

    # Prompt to overwrite if file already exists
    if element_snapshot_path.is_file():
        overwrite = input('The file already exists, do you want to overwrite it? [Y/N]: ')
        if str(overwrite) == '1' or str(overwrite).upper() == 'Y':
            break
        else:
            continue
    else:
        break


# Fetch API results
r = requests.get(fpl_api_bootstrap_static_url).json()

# Endpoint loop
for endpoint in api_endpoints:
    df = pd.DataFrame(r[endpoint])

    # File path to output
    output_file_path = str(fpl_api_raw_directory) + r'/'+endpoint + '.csv'

    # Store elements as snapshots
    if endpoint == 'elements':
        df['gameweek'] = gameweek
        df.to_csv(str(fpl_api_raw_directory) + r'/element_snapshots/elements_gw'+gameweek + '.csv',index=False)

    # Store current state for all endpoints
    df.to_csv(output_file_path,index=False)