#!/bin/bash
set -e

while IFS= read -r line; do
    echo $line
    ./project_status.py $line
    pdflatex -output-directory outputs outputs/$line.tex
done < $1
