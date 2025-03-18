import re
from datetime import datetime, timezone, timedelta
import nextcord
import asyncio
import pickle
from nextcord.ext import tasks, commands

# Replace these with your actual IDs and bot token
TOKEN = 'PUT BOT TOKEN HERE'  # Replace with your bot's token
GUILD_ID = 0       # Replace with the guild (server) ID as an integer
CHANNEL_ID = 0     # Replace with the channel ID to fetch messages from
OUTCHANNEL_ID = 0  # Replace with channel ID to send results to

EXCLUDECHANNELNAME = '#AFK'

# Set up intents
intents = nextcord.Intents.all()
intents.message_content = True  # Enable access to message content

# Create a bot instance
bot = commands.Bot(intents=intents, command_prefix='!')  # You can change the command prefix if you like

# Global data storage
data = {}

def load_data():
    global data
    try:
        with open('voice-data.pkl', 'rb') as f:
            data = pickle.load(f)
            print('Data loaded from file.')
    except FileNotFoundError:
        data = {
            'last_processed_message_id': None,
            'user_sessions': {},        # {user_id: {'channels': {...}, 'last_in_voice_channel_date': datetime}}
            'user_message_ids': {},     # {user_id: message_id}
        }
        print('No data file found. Starting fresh.')

def save_data():
    global data
    with open('voice-data.pkl', 'wb') as f:
        pickle.dump(data, f)
        print('Data saved to file.')

def parse_message(message):
    # This function will parse the message and return a dictionary:
    # {'event_type': 'join/leave/switch', 'channel': '#channel', 'from_channel': '#from', 'to_channel': '#to'}
    join_pattern = r'\*\*(.*?)\*\* joined (#\S+)'
    leave_pattern = r'\*\*(.*?)\*\* left (#\S+)'
    switch_pattern = r'\*\*Before:\*\* (#\S+)\n\*\*\+After:\*\* (#\S+)'

    # Check for join
    join_match = re.match(join_pattern, message)
    if join_match:
        # username = join_match.group(1).strip()
        channel = join_match.group(2).strip()
        return {'event_type': 'join', 'channel': channel}

    # Check for leave
    leave_match = re.match(leave_pattern, message)
    if leave_match:
        # username = leave_match.group(1).strip()
        channel = leave_match.group(2).strip()
        return {'event_type': 'leave', 'channel': channel}

    # Check for switch
    switch_match = re.match(switch_pattern, message, re.DOTALL)
    if switch_match:
        from_channel = switch_match.group(1).strip()
        to_channel = switch_match.group(2).strip()
        return {'event_type': 'switch', 'from_channel': from_channel, 'to_channel': to_channel}

    # Other messages are not relevant for session tracking
    return None

async def process_events(data_list):
    global data
    user_sessions = data['user_sessions']
    incomplete_entries = []

    # Sort data_list by timestamp
    data_list.sort(key=lambda x: x[1])

    for user_id, timestamp, message_content in data_list:
        event_info = parse_message(message_content)
        if event_info is None:
            continue  # Ignore irrelevant messages

        # Initialize user data if not present
        if user_id not in user_sessions:
            user_sessions[user_id] = {'channels': {}, 'last_in_voice_channel_date': None}

        user_data = user_sessions[user_id]
        channels_data = user_data['channels']

        event_type = event_info['event_type']

        if event_type == 'join':
            channel = event_info['channel']
            # Initialize channel data if not present
            if channel not in channels_data:
                channels_data[channel] = {'sessions': [], 'current_status': 'left'}
            channel_data = channels_data[channel]
            if channel_data['current_status'] == 'left':
                channel_data['sessions'].append({'join': timestamp})
                channel_data['current_status'] = 'joined'
            else:
                # Handle missing leave event
                # Log the timestamp and omit the previous incomplete session
                last_session = channel_data['sessions'].pop()
                incomplete_entries.append((user_id, channel, 'missing leave', last_session['join']))
                channel_data['sessions'].append({'join': timestamp})
                channel_data['current_status'] = 'joined'
        elif event_type == 'leave':
            channel = event_info['channel']
            if channel not in channels_data or channels_data[channel]['current_status'] == 'left':
                # Missing join event before leave
                incomplete_entries.append((user_id, channel, 'missing join', timestamp))
                # Since there was no join, we can't process this leave
                continue
            channel_data = channels_data[channel]
            if channel_data['current_status'] == 'joined':
                last_session = channel_data['sessions'][-1]
                last_session['leave'] = timestamp
                channel_data['current_status'] = 'left'
                # Update last_in_voice_channel_date
                user_data['last_in_voice_channel_date'] = timestamp
            else:
                # Missing join event
                incomplete_entries.append((user_id, channel, 'missing join', timestamp))
        elif event_type == 'switch':
            from_channel = event_info['from_channel']
            to_channel = event_info['to_channel']

            # Handle leaving from_channel
            if from_channel not in channels_data:
                channels_data[from_channel] = {'sessions': [], 'current_status': 'left'}
            from_channel_data = channels_data[from_channel]
            if from_channel_data['current_status'] == 'joined':
                last_session = from_channel_data['sessions'][-1]
                last_session['leave'] = timestamp
                from_channel_data['current_status'] = 'left'
                # Update last_in_voice_channel_date
                user_data['last_in_voice_channel_date'] = timestamp
            else:
                # Missing join event for from_channel
                incomplete_entries.append((user_id, from_channel, 'missing join', timestamp))

            # Handle joining to_channel
            if to_channel not in channels_data:
                channels_data[to_channel] = {'sessions': [], 'current_status': 'left'}
            to_channel_data = channels_data[to_channel]
            if to_channel_data['current_status'] == 'left':
                to_channel_data['sessions'].append({'join': timestamp})
                to_channel_data['current_status'] = 'joined'
            else:
                # Missing leave event for to_channel
                # Log the timestamp and omit the previous incomplete session
                last_session = to_channel_data['sessions'].pop()
                incomplete_entries.append((user_id, to_channel, 'missing leave', last_session['join']))
                to_channel_data['sessions'].append({'join': timestamp})
                to_channel_data['current_status'] = 'joined'
        else:
            pass  # Ignore other events

    # Save updated user_sessions back to data
    data['user_sessions'] = user_sessions

async def update_user_totals():
    global data
    guild = bot.get_guild(GUILD_ID)
    output_channel = bot.get_channel(OUTCHANNEL_ID)
    if output_channel is None:
        print(f'Could not find output channel with ID {OUTCHANNEL_ID}')
        return

    user_sessions = data['user_sessions']
    user_message_ids = data['user_message_ids']

    total_durations_per_user = {}  # {user_id: {'channels': {channel: total_duration}, 'subtotal': duration, 'last_in_voice_channel_date': datetime}}
    for user_id, user_data in user_sessions.items():
        channels_data = user_data['channels']
        total_durations = {}
        subtotal = 0
        for channel, channel_data in channels_data.items():
            # Complete any sessions still open at the end up to 24 hours
            sessions_to_keep = []
            for session in channel_data['sessions']:
                if 'join' in session and 'leave' in session:
                    duration = (session['leave'] - session['join']).total_seconds()
                    if duration <= 86400:  # 24 hours in seconds
                        sessions_to_keep.append(session)
                    else:
                        # Omit sessions over 24 hours
                        pass
                else:
                    # Missing join or leave; omit
                    pass
            channel_data['sessions'] = sessions_to_keep

            total_duration = sum((session['leave'] - session['join']).total_seconds() for session in channel_data['sessions'])
            total_durations[channel] = total_duration
            if channel != EXCLUDECHANNELNAME:  # Exclude EXCLUDECHANNELNAME from subtotal
                subtotal += total_duration

        total_durations_per_user[user_id] = {
            'channels': total_durations,
            'subtotal': subtotal,
            'last_in_voice_channel_date': user_data['last_in_voice_channel_date']
        }

    # Now, update or send messages for each user
    for user_id, totals_info in total_durations_per_user.items():
        member = guild.get_member(int(user_id))
        if member is None:
            username = f'User ID {user_id}'
        else:
            username = str(member.display_name)

        message_content = f'**{username}**\n'
        message_content += f'Total durations:\n'
        for channel, total_duration in totals_info['channels'].items():
            hours, remainder = divmod(total_duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            message_content += f'  {channel}: {int(hours)}h {int(minutes)}m {int(seconds)}s\n'

        # Include subtotal excluding EXCLUDECHANNELNAME
        subtotal_duration = totals_info['subtotal']
        hours, remainder = divmod(subtotal_duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        message_content += f'\n**Subtotal (excluding {EXCLUDECHANNELNAME}): {int(hours)}h {int(minutes)}m {int(seconds)}s**\n'

        # Include 'last in voice channel date'
        last_date = totals_info['last_in_voice_channel_date']
        if last_date:
            last_date_str = last_date.strftime('%Y-%m-%d %H:%M:%S UTC')
            message_content += f'Last in voice channel: {last_date_str}'
        else:
            message_content += 'No voice channel activity recorded.'

        # Now, update or send message
        if user_id in data['user_message_ids']:
            message_id = data['user_message_ids'][user_id]
            try:
                message = await output_channel.fetch_message(message_id)
                await message.edit(content=message_content)
            except nextcord.NotFound:
                # Message not found, send a new one
                message = await output_channel.send(message_content)
                data['user_message_ids'][user_id] = message.id
        else:
            # Send new message
            message = await output_channel.send(message_content)
            data['user_message_ids'][user_id] = message.id

    # Save data after updating messages
    save_data()

@tasks.loop(minutes=1)
async def fetch_and_process_messages(process_all=False):
    global data
    # Get the guild (server) object
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print('Guild not found')
        return

    # Get the channel object from the guild
    channel = guild.get_channel(CHANNEL_ID)
    if channel is None:
        print('Channel not found in the specified guild.')
        return

    try:
        last_processed_message_id = data['last_processed_message_id']
        data_list = []
        if last_processed_message_id is None or process_all:
            # Fetch all messages
            print('Fetching all messages...')
            messages = []
            async for message in channel.history(limit=None, oldest_first=True):
                messages.append(message)
                # Sleep briefly to respect rate limits
                await asyncio.sleep(0.1)
            if messages:
                print(f'Fetched {len(messages)} messages.')
                for message in messages:
                    try:
                        if 'Carl-bot' in str(message.author):
                            user_id = message.embeds[0].footer.text[4:]
                            timestamp = message.embeds[0].timestamp
                            message_content = message.embeds[0].description
                            data_list.append((user_id, timestamp, message_content))
                        else:
                            print('Non-logging bot message!')
                    except Exception as e:
                        print(f'An error occurred while processing a message: {e}')
                # Update last_processed_message_id
                data['last_processed_message_id'] = messages[-1].id
                # Process events
                await process_events(data_list)
                # Update user totals and messages
                await update_user_totals()
                # Save data
                save_data()
            else:
                print('No messages found in the channel.')
        else:
            # Fetch messages after last_processed_message_id
            messages = []
            async for message in channel.history(after=nextcord.Object(id=last_processed_message_id), oldest_first=True):
                messages.append(message)
                # Sleep briefly to respect rate limits
                await asyncio.sleep(0.1)
            if messages:
                print(f'Fetched {len(messages)} new messages.')
                for message in messages:
                    try:
                        if 'Carl-bot' in str(message.author):
                            user_id = message.embeds[0].footer.text[4:]
                            timestamp = message.embeds[0].timestamp
                            message_content = message.embeds[0].description
                            data_list.append((user_id, timestamp, message_content))
                        else:
                            print('Non-logging bot message!')
                    except Exception as e:
                        print(f'An error occurred while processing a message: {e}')
                # Update last_processed_message_id
                data['last_processed_message_id'] = messages[-1].id
                # Process events
                await process_events(data_list)
                # Update user totals and messages
                await update_user_totals()
                # Save data
                save_data()
            else:
                print('No new messages to process.')
    except Exception as e:
        print(f'An error occurred: {e}')

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    # Load data
    load_data()
    # Start background task
    fetch_and_process_messages.start()

@bot.command()
@commands.has_permissions(administrator=True)
async def loadall(ctx):
    """Admin command to load and process all messages in the log channel."""
    await ctx.author.send('Starting to load and process all messages in the log channel. This may take some time.')
    # Reset last_processed_message_id to None
    data['last_processed_message_id'] = None
    # Clear user_sessions and user_message_ids to start fresh
    data['user_sessions'] = {}
    data['user_message_ids'] = {}
    save_data()
    # Stop the existing loop to prevent overlap
    fetch_and_process_messages.cancel()
    # Start processing all messages
    await fetch_and_process_messages(process_all=True)
    # Restart the regular loop
    fetch_and_process_messages.start()
    await ctx.author.send('Finished processing all messages.')

@loadall.error
async def loadall_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send('You do not have permission to execute this command.')
    elif isinstance(error, commands.CommandInvokeError):
        await ctx.send('An error occurred while executing the command.')
    else:
        await ctx.send(f'An unexpected error occurred: {error}')

# Run the bot
bot.run(TOKEN)
