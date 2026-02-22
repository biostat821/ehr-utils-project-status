#!/bin/bash
set -e

echo "name,username,phase,pr,score" > "outputs/_summary.csv"
echo "name,username,phase,pr,state,late_by,waiting_for" > "outputs/_state_summary.csv"
while IFS=, read -r email name last
do
    username=${last%$'\r'}
    echo $username
    ./project_status.py $username "$name"
    if [ -f "outputs/$username.tex" ]; then
        pdflatex -output-directory outputs outputs/$username.tex
        echo "outputs/$username.pdf"
    fi
    # osascript sendMail.scpt $username $email $name
done  < <(tail -n +2 $1)
