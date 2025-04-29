#!/bin/bash
set -e

echo "name,username,phase,pr,score" > "outputs/_summary.csv"
while IFS=, read -r email name last
do
    username=${last%$'\r'}
    echo $username
    ./project_status.py $username "$name"
    pdflatex -output-directory outputs outputs/$username.tex
    echo "outputs/$username.pdf"
    osascript sendMail.scpt $username $email $name
done  < <(tail -n +2 $1)
