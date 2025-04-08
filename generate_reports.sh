#!/bin/bash
set -e

while IFS=, read -r email name last
do
    username=${last%$'\r'}
    echo $username
    ./project_status.py $username
    pdflatex -output-directory outputs outputs/$username.tex
    echo "outputs/$username.pdf"
    osascript sendMail.scpt $username $email $name
done  < <(tail -n +2 $1)
