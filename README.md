# Spack Packages Recipes Manager
# Spack Pull Requests Manager
# Spack Patch Rebase Manager
The idea is to experiment a tool to help in mantaining a series of branches  of spack packages recipes, 
ideally of separate concerns, that could be submitted as PR in spack packages upstream or kept as branch patches
and used to build overlay repos of recipes for specific  environments

First implementation experiment, Google AI track in pdf, some minor bugfix

to start

make setup

python sprm.py --path <local integration folder> --debug
