# Config file
from pathlib import Path

# file paths
main_directory = Path(__file__).parent.parent.parent
fpl_api_raw_directory = Path(str(main_directory)+r'/data/raw/fpl_api/')

# urls
fpl_api_base_url = r'https://fantasy.premierleague.com/api/'
fpl_api_bootstrap_static_url = fpl_api_base_url + 'bootstrap-static/'