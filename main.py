import discord
import datetime
import websockets
import asyncio
import math
import json
import requests
import logging
import time

import sqlite3

# from config import API_KEY # BOT_TOKEN,
BOT_TOKEN = ''
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')


class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.zkill_websocket = None

        # Create a database connection and cursor
        self.db_connection = sqlite3.connect('filter_db.sqlite')
        self.db_cursor = self.db_connection.cursor()

        # Create the filter table if it doesn't exist
        self.db_cursor.execute('''
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                channel_id TEXT,
                home_system TEXT,
                ship_type_group TEXT,
                ly_range REAL
            )
        ''')

        self.db_connection.commit()


    async def on_ready(self):
        print(f"Logged in as {self.user.name} ({self.user.id})")

        while True:
            try:
                # Set up WebSocket connection
                print("Connecting to zKillboard WebSocket")
                self.zkill_websocket = await websockets.connect('wss://zkillboard.com/websocket/')
                print("Sending subscribe command")
                subscribe_command = {
                    "action": "sub",
                    "channel": "killstream"
                }
                await self.zkill_websocket.send(json.dumps(subscribe_command))

                # Start listening for WebSocket responses
                print("Waiting for responses")
                while True:
                    response = await self.zkill_websocket.recv()
                    response_data = json.loads(response)

                    # Filter based on the saved filters
                    filters = self.get_filters()
                    # Create a dictionary to group attackers based on filters
                    attackers_by_filter = {}

                    for attacker in response_data["attackers"]:
                        attacker_ship_id = attacker.get("ship_type_id")
                        attacker_location_id = response_data["solar_system_id"]
                        attacker_coordinates = get_system_coordinates(attacker_location_id, self.db_connection, self.db_cursor)
                        attacker_security = get_system_security(attacker_location_id, self.db_connection, self.db_cursor)

                        if attacker_ship_id is not None and attacker_coordinates is not None and attacker_security is not None and attacker_security <= 0.499:
                            for filter_data in filters:
                                home_system, ship_type_group, ly_range = filter_data[3], filter_data[4], filter_data[5]
                                home_system_coordinates = get_system_coordinates(home_system, self.db_connection, self.db_cursor)

                                if home_system_coordinates is not None:
                                    ly_distance = calculate_ly_distance(home_system_coordinates, attacker_coordinates)
                                    print(f"ly distance is {ly_distance}")

                                    if (attacker_ship_id == ship_type_group or is_ship_in_group(attacker_ship_id, ship_type_group, self.db_connection, self.db_cursor)) and ly_distance <= ly_range:
                                        zkill_url = response_data["zkb"].get("url")
                                        filter_key = (home_system, ship_type_group, ly_range)
                                        if filter_key not in attackers_by_filter:
                                            attackers_by_filter[filter_key] = []
                                        attackers_by_filter[filter_key].append((response_data, zkill_url, ly_distance))  # Include ly_distance here

                    # Send one message per filter with matching attackers
                    for filter_key, attackers in attackers_by_filter.items():
                        home_system, ship_type_group, ly_range = filter_key
                        channel_id = filters[0][2]  # Assuming the same channel for all filters

                        if attackers:  # Check if there are matching attackers for this filter
                            channel = self.get_channel(int(channel_id))
                            if channel:
                                await self.send_kill_report(attackers, home_system, ship_type_group, ly_range, channel)
                            else:
                                print("ZKill URL not sent. Channel not found.")

            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed. Reconnecting...")
                # Sleep for a short while before attempting to reconnect
                await asyncio.sleep(5)
            except Exception as e:
                print("An error occurred:", e)
                # Sleep for a short while before attempting to continue
                await asyncio.sleep(5)


    async def send_kill_report(self, attackers, home_system, ship_type_group, ly_range, channel):
        # Initialize variables to store values
        zkill_url = ""
        attacker_system_id = ""
        ly_distance = 0.0

        if attackers:
            for attacker_info in attackers:
                if len(attacker_info) >= 3:
                    response_data = attacker_info[0]
                    zkill_url = attacker_info[1]
                    ly_distance = attacker_info[2]  # Extract ly_distance

                    # Get the attacker's system ID (assuming it's the same for all attackers)
                    if "solar_system_id" in response_data:
                        attacker_system_id = response_data["solar_system_id"]
                        break

            # Calculate the jumps from the home system to the attacker's system
            jumps = calculate_number_of_jumps(home_system, attacker_system_id) if attacker_system_id else ""

            # Look up the home system name from the database
            home_system_name = get_system_name_by_id(home_system, self.db_connection, self.db_cursor)

            attacker_system_name = get_system_name_by_id(attacker_system_id, self.db_connection, self.db_cursor)

            # Look up the ship group name from the database
            group_name = get_group_name(ship_type_group, self.db_connection, self.db_cursor)

            report_message = (
                f"**ZKill URL:** {zkill_url}\n\n"
                f"🔥 **Target Spotted!**\n\n"
                f"located at:** {attacker_system_name}\n"
                f"**Group Name:** {group_name}\n"
                f"**Jumps from {home_system_name}:** {jumps}\n"
                f"**LY Distance: {ly_distance:.2f} LY\n"
                
            )

            await channel.send(report_message)
            print("Kill report sent:", report_message)



    async def on_message(self, message):
        if message.author == self.user:
            return
        
                # Handle the !evetime command
        if message.content.startswith('!evetime'):
            evetime = self.get_eve_time()
            await message.reply(f"**Current Eve Online time:** `{evetime}`", mention_author=True)

                # Handle the !price command
        #if message.content.startswith('!price'):
         #   content = message.content[len('!price'):].strip()
          #  response = process_price_command(content)

           # await message.reply(response, mention_author=True)
    
        args = message.content.split()  # Split the message content into arguments

        if args[0] == '!hunter':
            if len(args) == 1:
                await message.channel.send("Available commands:\n"
                                        "!hunter add_filter home_system ship_type_or_group ly_range\n"
                                        "!hunter delete_filter filter_id\n"
                                        "!hunter list_filters\n"
                                        "!hunter help")
                return

            if args[1] == 'add_filter':
                if len(args) < 5:  # Updated to match the number of arguments needed
                    await message.channel.send("Invalid command usage. Use: !hunter add_filter home_system ship_type_or_group ly_range")
                    return

                # Get the server ID and channel ID from the message context
                server_id = str(message.guild.id)
                channel_id = str(message.channel.id)

                home_system = args[2]  # Updated index
                ly_range = float(args[-1])  # Last argument

                # Combine the middle arguments into the ship type/group name
                ship_type_or_group = ' '.join(args[3:-1])  # Updated indices

                # Check if the provided home system exists in the solar_systems table
                home_system_id = get_system_id_by_name(home_system, self.db_connection, self.db_cursor)
                if home_system_id is None:
                    await message.channel.send("The specified home system does not exist.")
                    return

                # Fetch ship type ID from the ship_groups table using the ship name or group name
                ship_type_id = get_ship_type_id(ship_type_or_group, self.db_connection, self.db_cursor)
                if ship_type_id is None:
                    await message.channel.send("The specified ship type or group does not exist.")
                    return

                # Save the filter with server and channel IDs
                self.save_filter(server_id, channel_id, home_system_id, ship_type_id, ly_range)
                await message.channel.send("Filter added successfully.")
                return

            elif args[1] == 'delete_filter':
                if len(args) == 3:
                    filter_id = int(args[2])
                    deleted = self.delete_filter(filter_id)
                    if deleted:
                        await message.channel.send(f"Filter with ID {filter_id} deleted.")
                    else:
                        await message.channel.send(f"Filter with ID {filter_id} not found.")
                else:
                    await message.channel.send("Invalid command usage. Use: !hunter delete_filter filter_id")
                return

            elif args[1] == 'list_filters':
                filters = self.get_filters()
                if filters:
                    filter_list = []
                    for row in filters:
                        filter_id = row[0]
                        home_system_id = row[3]
                        ship_type_group = row[4]
                        ly_range = row[5]

                        home_system_name = get_system_name_by_id(home_system_id, self.db_connection, self.db_cursor)
                        group_name = get_group_name(ship_type_group, self.db_connection, self.db_cursor)

                        filter_list.append(f"ID: {filter_id}, Home System: {home_system_name}, Group Name: {group_name}, LY Range: {ly_range}")

                    filter_message = "\n".join(filter_list)
                    await message.channel.send(f"**Filters:**\n{filter_message}")
                else:
                    await message.channel.send("No filters found.")
                return

            elif args[1] == 'help':
                await message.channel.send("Available commands:\n"
                                        "!hunter add_filter home_system ship_type_or_group ly_range\n"
                                        "!hunter delete_filter filter_id\n"
                                        "!hunter list_filters\n"
                                        "!hunter help")
                return

            else:
                await message.channel.send("Unknown command. Type `!hunter help` for a list of available commands.")
                return
                
        # New command: !distance system1 system2
        elif args[0] == '!distance':
            if len(args) == 3:
                system1 = args[1]
                system2 = args[2]

                # Fetch system IDs from the database
                system1_id = get_system_id_by_name(system1, self.db_connection, self.db_cursor)
                system2_id = get_system_id_by_name(system2, self.db_connection, self.db_cursor)

                if system1_id is None or system2_id is None:
                    await message.channel.send("One or both of the specified systems do not exist.")
                    return

                # Calculate LY distance and number of jumps
                system1_coords = get_system_coordinates(system1_id, self.db_connection, self.db_cursor)
                system2_coords = get_system_coordinates(system2_id, self.db_connection, self.db_cursor)

                if system1_coords is None or system2_coords is None:
                    await message.channel.send("Error fetching system coordinates.")
                    return

                ly_distance = calculate_ly_distance(system1_coords, system2_coords)
                jumps = calculate_number_of_jumps(system1_id, system2_id)

                await message.channel.send(f"Distance between {system1} and {system2}: {ly_distance:.2f} LY\n"
                                        f"Number of jumps: {jumps}")
            else:
                await message.channel.send("Invalid command usage. Use: !distance system1 system2")


    def delete_filter(self, filter_id):
        self.db_cursor.execute('DELETE FROM filters WHERE id = ?', (filter_id,))
        self.db_connection.commit()
        return self.db_cursor.rowcount > 0
    def save_filter(self, server_id, channel_id, home_system_id, ship_identifier, ly_range):
        # Insert a new filter into the database
        self.db_cursor.execute('''
            INSERT INTO filters (server_id, channel_id, home_system, ship_type_group, ly_range)
            VALUES (?, ?, ?, ?, ?)
        ''', (server_id, channel_id, home_system_id, ship_identifier, ly_range))
        self.db_connection.commit()
    def get_filters(self):
        # Retrieve all filters from the database
        self.db_cursor.execute('SELECT * FROM filters')
        return self.db_cursor.fetchall()
    
    def get_eve_time(self):
        now = datetime.datetime.utcnow()
        eve_time = now.strftime("%H:%M")
        return eve_time

# Define a function to process the !price command
import requests
# from config import API_KEY

import requests

def process_price_command(content):
    try:
        # Define the API endpoint and parameters
        api_url = "https://janice.e-351.com/api/rest/v2/appraisal"
        params = {
            "market": "2",
            "persist": "true",
            "compactize": "true",
            "pricePercentage": "1"
        }

        # Define the headers with X-ApiKey
        headers = {
            "accept": "application/json",
            "X-ApiKey": API_KEY,
            "Content-Type": "text/plain"
        }

        # Make the POST request with data from content
        response = requests.post(api_url, params=params, headers=headers, data=content)

        if response.status_code == 200:
            # Request was successful, process the response data
            response_data = response.json()
            
            # Extract immediatePrices and code directly from the response
            total_sell_price = response_data["immediatePrices"]["totalSellPrice"]
            formatted_total_sell_price = f"{total_sell_price:,}"  # Format the price with commas
            code = response_data["code"]
            
            # Create a response message
            response_message = (
                f"Total Sell Price: {formatted_total_sell_price}\n"
                f"https://janice.e-351.com/a/{code}"
            )
            
            return response_message
        
        else:
            return f"Request failed with status code {response.status_code}: call your grandson you silly ass boomer"
    except Exception as e:
        return f"Error processing !price command: {str(e)}"


# Add a dictionary to store cached route data and their timestamps
cached_routes = {}

# Set the cache expiration time to 1 week in seconds
CACHE_EXPIRATION_TIME = 7 * 24 * 60 * 60

def calculate_number_of_jumps(origin_system_id, destination_system_id):
    cached_route = cached_routes.get((origin_system_id, destination_system_id))
    
    if cached_route and time.time() - cached_route['timestamp'] < CACHE_EXPIRATION_TIME:
        jumps = len(cached_route['data']) - 1
        return jumps
    else:
        esi_url = f"https://esi.evetech.net/latest/route/{origin_system_id}/{destination_system_id}/"
        response = requests.get(esi_url)

        if response.status_code == 200:
            route_data = response.json()
            jumps = len(route_data) - 1

            # Cache the route data with a timestamp
            cached_routes[(origin_system_id, destination_system_id)] = {'data': route_data, 'timestamp': time.time()}

            return jumps
        else:
            print("Error fetching route data from ESI.")
            return None
        


def get_group_name(group_id, db_connection, db_cursor):
    try:
        query = 'SELECT group_name FROM ship_groups WHERE group_id = ?'
        result = db_cursor.execute(query, (group_id,)).fetchone()

        if result:
            group_name = result[0]
            return group_name
        else:
            return "Unknown Group"
    except sqlite3.Error as e:
        print(f"Error fetching group name for group ID {group_id}: {e}")
        return "Error"

def get_ship_type_id(ship_type_or_group, db_connection, db_cursor):
    try:
        query = 'SELECT type_id, group_id FROM ship_groups WHERE LOWER(group_name) = ? OR LOWER(type_name) = ?'
        result = db_cursor.execute(query, (ship_type_or_group.lower(), ship_type_or_group.lower())).fetchone()

        if result:
            type_id, group_id = result
            ship_type_id = group_id if group_id is not None else type_id
            return ship_type_id
        else:
            return None
    except sqlite3.Error as e:
        print(f"Error fetching ship type ID for ship type or group {ship_type_or_group}: {e}")
        return None

  
def get_system_id_by_name(system_name, db_connection, db_cursor):
    try:
        print(f"Fetching system ID for system name: {system_name}")

        query = 'SELECT solarSystemID FROM solar_systems WHERE LOWER(solarSystemName) = ?'
        result = db_cursor.execute(query, (system_name.lower(),)).fetchone()

        if result:
            system_id = result[0]
            print(f"System ID retrieved: {system_id}")
            return system_id
        else:
            print("System ID not found for the given system name.")
            return None

    except sqlite3.Error as e:
        print(f"Error fetching system ID for system name {system_name}: {e}")
        return None
    
def get_system_name_by_id(system_id, db_connection, db_cursor):
    try:
        query = 'SELECT solarSystemName FROM solar_systems WHERE solarSystemID = ?'
        result = db_cursor.execute(query, (system_id,)).fetchone()

        if result:
            system_name = result[0]
            return system_name
        else:
            return None
    except sqlite3.Error as e:
        print(f"Error fetching system name for system ID {system_id}: {e}")
        return None



def get_system_coordinates(system_id, db_connection, db_cursor):
    try:
        print(f"Fetching system coordinates for system ID: {system_id}")

        # Fetch system coordinates from the database
        query = 'SELECT x, y, z FROM solar_systems WHERE solarSystemID = ?'
        result = db_cursor.execute(query, (system_id,)).fetchone()

        if result:
            x, y, z = result
            coordinates = {"x": x, "y": y, "z": z}
            print(f"Coordinates retrieved: {coordinates}")
            return coordinates
        else:
            print("Coordinates not found for the given system ID.")

            # Lookup coordinates using ESI
            esi_url = f"https://esi.evetech.net/latest/universe/systems/{system_id}/"
            response = requests.get(esi_url)

            if response.status_code == 200:
                system_data = response.json()
                x = system_data["position"]["x"]
                y = system_data["position"]["y"]
                z = system_data["position"]["z"]
                security = system_data["security_status"]
                system_name = system_data["name"]  # Get the system name from ESI

                # Add the coordinates to the database
                insert_query = 'INSERT INTO solar_systems (solarSystemID, solarSystemName, x, y, z, security) VALUES (?, ?, ?, ?, ?, ?)'
                db_cursor.execute(insert_query, (system_id, system_name, x, y, z, security))
                db_connection.commit()

                coordinates = {"x": x, "y": y, "z": z}
                print(f"Coordinates retrieved from ESI and added to the database: {coordinates}")
                return coordinates
            else:
                print("Error fetching system data from ESI.")
                return None

    except sqlite3.Error as e:
        print(f"Error fetching system coordinates for system ID {system_id}: {e}")
        return None



def calculate_ly_distance(coord1, coord2):
    x_diff = coord1["x"] - coord2["x"]
    y_diff = coord1["y"] - coord2["y"]
    z_diff = coord1["z"] - coord2["z"]
    distance = math.sqrt(x_diff ** 2 + y_diff ** 2 + z_diff ** 2)
    ly_distance = distance / 9460730472580.04 / 1000
    return ly_distance

def is_ship_in_group(attacker_ship_id, ship_type_group, db_connection, db_cursor):
    try:
        query = 'SELECT group_id FROM ship_groups WHERE type_id = ?'
        result = db_cursor.execute(query, (attacker_ship_id,)).fetchone()

        if result:
            ship_group_id = result[0]

            if ship_group_id is None:
                print("Ship group ID is None")
                return False

            # Convert ship_group_id to integer for comparison
            ship_group_id = int(ship_group_id)

            # Convert ship_type_group to integer if needed
            if not isinstance(ship_type_group, int):
                ship_type_group = int(ship_type_group)

            print(f"Ship group ID: {ship_group_id}, Ship type/group ID: {ship_type_group}")

            if ship_group_id == ship_type_group:
                print("Ship is in the specified group")
                return True
            else:
                print(f"Ship is not in the specified group {ship_group_id}")
                return False
        else:
            print(f"No result found for ship ID{result}")
            return False
    except sqlite3.Error as e:
        print(f"Error checking ship group for ship ID {attacker_ship_id}: {e}")
        return False

def get_system_security(system_id, db_connection, db_cursor):
    try:
        query = 'SELECT security FROM solar_systems WHERE solarSystemID = ?'
        result = db_cursor.execute(query, (system_id,)).fetchone()

        if result:
            security_level = result[0]
            return security_level
        else:
            return None
    except sqlite3.Error as e:
        print(f"Error fetching system security for system ID {system_id}: {e}")
        return None


intents = discord.Intents.default()
intents.message_content = True
# 
client = MyClient(intents=intents)
client.run(BOT_TOKEN, log_handler=handler)