# 1. Create an empty environment forced to Intel architecture
CONDA_SUBDIR=osx-64 conda create -n pymushra python=3.12

# 2. Activate it
conda activate pymushra

# 3. Permanently set this env to use Intel packages so they don't conflict
conda config --env --set subdir osx-64

pip install -r requirements.txt