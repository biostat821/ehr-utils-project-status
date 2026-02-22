on run argv
  tell application "Microsoft Outlook"
    set theFile to "/Users/patrick/Documents/Duke/teaching/BIOSTAT821/activities/github/ehr-utils-project-status/outputs/" & (item 1 of argv) & ".pdf" as POSIX file
    set theContent to "Hi " & (item 3 of argv) & ",<br><br>Please find attached the latest report for your EHR project. Let me know if you have any questions or concerns.<br><br>Best,<br>-Patrick"
    set theMessage to make new outgoing message with properties {subject:"[BIOSTAT 821] EHR Project Status",content:theContent}
    make new recipient at theMessage with properties {type:to recipient type, email address:{address:(item 2 of argv)}}
    tell theMessage to make new attachment with properties {file:theFile} at the end of theMessage
    open theMessage
  end tell
end run