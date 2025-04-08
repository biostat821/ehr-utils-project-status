#!/bin/bash
set -e

while IFS=, read -r email name username
do
    echo $username
    ./project_status.py $username
    pdflatex -output-directory outputs outputs/$username.tex
    echo "outputs/$username.pdf"
done  < <(tail -n +2 $1)
