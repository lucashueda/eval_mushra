# 1. Create an empty environment forced to Intel architecture
CONDA_SUBDIR=osx-64 conda create -n pymushra_env python=3.7

# 2. Activate it
conda activate pymushra_env

# 3. Permanently set this env to use Intel packages so they don't conflict
conda config --env --set subdir osx-64

# 4. Now install the rest of the requirements
conda install -c conda-forge click flask=2.2.5 ipython matplotlib numpy pandas patsy scipy seaborn statsmodels tinydb

# 5. Install the pip-only extras
pip install tinyrecord hatchling

pip install gspread oauth2client google-api-python-client google-auth-httplib2 google-auth-oauthlib