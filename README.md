# voicelog-from-carlbot-to-pickle
Python script that takes join and leave log messages made by carlbot and stores the timestamp associated, and finds the totals for each and overall with the option for an excluded voice channel (for example an AFK channel) in a pickle file.

## Note:
This assumes you have a dedicated discord channel set up for carlbot to post voice channel event logs.

## Usage
Be sure you have python 3 and nextcord-py installed. 
Modify the python file to use your corrisponding bot token, guild id, log channel id, output channel id, and the name of any channels you want to be kept separate from the total time spent in voice channels calculation.

This bot script can be ran continuously or as desired. 


## Adendum:
I chose pickle because after 100 000 entries the size of a json file is rather large, about 5 times as large.
You can use https://gist.github.com/romgapuz/c7a4cedb85f090ac1b55383a58fa572c to convert the pickle file to a json file.
