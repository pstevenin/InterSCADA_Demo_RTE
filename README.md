# RTE Demonstrator

This demonstrator is part of the InterSCADA European project. It aims to run...

## Requirements
- Python 3.10+
- Dynawo Inputs
- EEAC Inputs

## Install
Install each of the following tools in the same folder.
### Install Dynawo
- Follow build instructions on Dynawo GitHub: https://github.com/dynawo/dynawo
- Git Checkout in fix_dynaswing branch
- Add line in myEnvDynawo.sh file:
```bash
export ADDITIONAL_INCLUDE_FOLDER="/usr/include/c++/11/;/usr/include/x86_64-linux-gnu/c++/11/"
```
- Modify line 744 in computeJacobian.py in dynawo/sources/ModelicaCompiler:
```bash
sp_res_expr = sp.sympify(rhs_res_expr, locals={'x': x, 'xd': xd, 'pow':sp.Pow , **sym_exprs})
```
- Final build:
```bash
pip install libclang==14.0.6
pip install clang-format==14.0.6
pip install simpy
./myEnvDynawo.sh build-all
./myEnvDynawo.sh deploy
```

### Install EEAC
```bash
git clone https://github.com/eRoots-Analytics/RTE-equal-area-criterion-testing EEAC
cd EEAC
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install build
python -m build
```

### Install Demo
```bash
git clone https://github.com/pstevenin/InterSCADA_Demo_RTE Demo
cd Demo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Quick start
Run with param_example.json configuration file:
```bash
python -m demo
```

Run with personal configuration file:
```bash
python -m demo --config /path/to/config.json
```

## Simulation Inputs

### Dynawo Inputs (all in same folder)
- Dynamic file (XML): *.dyd
- Network file (XML): *.iidm
- Configuration file (XML): *.jobs
- Parameters file (XML): *.par

### EEAC Input
- EEAC configuration file: eeac_param.json

### Faults Inputs (all in same folder)
- One file per fault: *.json
