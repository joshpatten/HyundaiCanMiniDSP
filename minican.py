#!/usr/bin/python3

import os
import can # pip install can
import cantools # pip install cantools
from minirs import minictl
import sys
from time import sleep
from datetime import datetime, timedelta
from queue import Queue
from threading import Thread
import subprocess
import configparser
import logging
from pprint import pformat


class G:
	config = None
	db = None
	candev = None
	max_vol = None
	left_main_input = None
	right_main_input = None
	left_notify_input = None
	right_notify_input = None
	veh_off_wait = None
	can_bitrate = None
	canint = None
	q = Queue()
	mini = None
	config_loc = None
	bin_loc = None

# Consts
VEH_POWER_OFF = 0
VEH_POWER_ON = 2
MUTE_ON = 1
MUTE_OFF = 0
NO_OP = "NoOp"
HU_VOLUME_STATUS = "HU_VolumeStatus"
HU_VEHICLE_POWER = "HU_VehiclePwr"
HU_MUTE_STATUS = "HU_MuteStatus"
INPUT = 'input'
OUTPUT = 'output'
MASTER = 'master'
MINVOL = -127.0
MAXVOL = 0

def volume_level(level: int, max_level: int) -> float:
	levperc = level/max_level
	vrange = abs(MINVOL)
	reduction = vrange * levperc
	return reduction + MINVOL


def set_vol(inttype = MASTER, level = 0.0, input = 1):
	try:
		if inttype == MASTER:
			G.mini.mainvolctl(level)
		elif inttype == INPUT:
			G.mini.inputvolctl(level, input)
		G.mini.submit()
	except Exception as e:
		print(f"Couldn't set volume for {inttype} input {input}: {e}")
	return

def can_init(device: str) -> bool:
	logging.debug(f'Starting up CANBUS interface `{device}`')
	os.system(f'/usr/sbin/ifconfig {device} down')
	logging.debug(f'Setting CANBUS interface `{device}` bitrate to `{G.can_bitrate}`')
	os.system(f'/usr/sbin/ip link set {device} type can bitrate {G.can_bitrate}')
	os.system(f'/usr/sbin/ifconfig {device} up')
	try:
		G.canint = can.interface.Bus(channel = device, bustype = 'socketcan_ctypes')
		return True
	except Exception as e:
		logging.error(f"Error initializing CANBUS device {device}: {e}")
		return False

def sys_shutdown():
	logging.info('Full system shutdown initiated')
	os.system("/usr/bin/systemctl poweroff")
	sys.exit(0)

def action_thread():
	volume = 0
	mute = 0
	vpower = 9
	vtimer = datetime.now()
	while True:
		data = G.q.get()
		if data is None:
			sleep(.05)
		else:
			validcmd = False
			if NO_OP in data:
				validcmd = True
			if HU_VEHICLE_POWER in data:
				validcmd = True
				if int(data[HU_VEHICLE_POWER]) == VEH_POWER_OFF and int(data[HU_VEHICLE_POWER]) != vpower:
					logging.info(f"Vehicle power off detected. Waiting for {G.veh_off_wait} seconds to see if vehicle is started up again, otherwise powering off")
					vpower = VEH_POWER_OFF
					vtimer = datetime.now()
				elif int(data[HU_VEHICLE_POWER]) != VEH_POWER_OFF and vpower == VEH_POWER_OFF:
					logging.info("Vehicle power off cancelled")
					vpower = int(data[HU_VEHICLE_POWER])
				else:
					vpower = int(data[HU_VEHICLE_POWER])
			if vpower == VEH_POWER_OFF:
				validcmd = True
				if (datetime.now() - timedelta(seconds=G.veh_off_wait) > vtimer):
					logging.info(f"Vehicle has been powered off for more than {G.veh_off_wait} seconds, shutting down...")
					sys_shutdown()
			if HU_VOLUME_STATUS in data:
				validcmd = True
				if int(data[HU_VOLUME_STATUS]) != volume:
					logging.info(f"Volume changed from {volume} to {data[HU_VOLUME_STATUS]}")
					set_vol(
						index=G.output_device,
						level=volume_level(
							int(
								data[HU_VOLUME_STATUS]
							),
							G.max_vol
						)
					)
					volume = data[HU_VOLUME_STATUS]
			if HU_MUTE_STATUS in data:
				validcmd = True
				if int(data[HU_MUTE_STATUS]) != mute:
					logging.info(f"Mute changed from {mute} to {data[HU_MUTE_STATUS]}")
					# Set to previous volume level
					if int(data[HU_MUTE_STATUS]) == MUTE_OFF:
						set_vol(
							index=G.output_device,
							level=volume_level(
								volume,
								G.max_vol
							)
						)
					else:
						set_vol(
							index=G.output_device,
							level=0.0
						)
					mute = int(data[HU_MUTE_STATUS])
			if not validcmd:
				logging.debug(f"Command not usable, skipping processing: `{pformat(data)}`")

def listen_loop(test):	
	logging.info('Test mode set, performing test commands')
	if test:
		logging.debug(f'Issuing NO_OP command: `{NO_OP}` with value `0`')
		G.q.put({NO_OP: 0})
		sleep(5)
		logging.debug(f'Issuing HU_VEHICLE_POWER command: `{HU_VEHICLE_POWER}` with value `0`')
		G.q.put({HU_VEHICLE_POWER: 0})
		sleep(5)
		logging.debug(f'Issuing HU_VOLUME_STATUS command: `{HU_VOLUME_STATUS}` with value `20`')
		G.q.put({HU_VOLUME_STATUS: 20})
		sleep(5)
		logging.debug(f'Issuing HU_VOLUME_STATUS command: `{HU_VOLUME_STATUS}` with value `20`')
		G.q.put({HU_VOLUME_STATUS: 30})
		sleep(5)
		logging.debug(f'Issuing HU_MUTE_STATUS command: `{HU_MUTE_STATUS}` with value `1`')
		G.q.put({HU_MUTE_STATUS: 1})
		sleep(5)
		logging.debug(f'Issuing HU_MUTE_STATUS command: `{HU_MUTE_STATUS}` with value `0`')
		G.q.put({HU_MUTE_STATUS: 0})
		sleep(5)
		logging.debug(f'Issuing HU_VEHICLE_POWER command: `{HU_VEHICLE_POWER}` with value `2`')
		G.q.put({HU_VEHICLE_POWER: 2})
		sleep(5)
		return
		
	
	while True:
		msg = G.canint.recv(10.0)
		try:
			data = G.db.decode_message(msg.arbitration_id, msg.data)
			logging.debug(f"CANBUS message received with the following data: `{pformat(data)}`")
			G.q.put(data)
		except (KeyError, AttributeError):
			logging.debug(f"Unknown CANBUS message received: `{pformat(msg)}`")
		if msg is None:
			logging.debug("No CANBUS message received, sending `NO_OP` to queue")
			G.q.put({NO_OP: 0})
	
def main(test=False):
	logging.info(f"Loading CANBUS database file: `{G.config.get('General', 'can_file')}`")
	G.db = cantools.database.load_file(G.config.get('General', 'can_file'))
	logging.debug(f"Setting CANBUS device to `{G.config.get('General', 'candev')}`")
	G.candev = G.config.get('General', 'candev')
	logging.debug(f"Setting max_vol parameter to `{G.config.get('General', 'max_vol')}`")
	G.max_vol = int(G.config.get('General', 'max_vol'))
	logging.debug(f"Setting vehicle turn-off timer to `{G.config.get('General', 'veh_off_wait')}` seconds")
	G.veh_off_wait = int(G.config.get('General', 'veh_off_wait'))
	logging.debug(f"Setting CANBUS bitrate to `{G.config.get('General', 'can_bitrate')}`")
	G.can_bitrate = int(G.config.get('General', 'can_bitrate'))
	logging.debug(f"Setting left main input to input `{G.config.get('MiniDSP', 'main_left')}`")
	G.left_main_input = int(G.config.get('MiniDSP', 'main_left'))
	logging.debug(f"Setting right main input to input `{G.config.get('MiniDSP', 'main_right')}`")
	G.right_main_input = int(G.config.get('MiniDSP', 'main_right'))
	logging.debug(f"Setting left notification input to input `{G.config.get('MiniDSP', 'notify_left')}`")
	G.left_notify_input = int(G.config.get('MiniDSP', 'notify_left'))
	logging.debug(f"Setting right notification input to input `{G.config.get('MiniDSP', 'notify_right')}`")
	G.right_notify_input = int(G.config.get('MiniDSP', 'notify_right'))
	logging.debug(f"Setting minidsp-rs config location to `{G.config.get('MiniDSP', 'config_loc')}`")
	G.config_loc = G.config.get('MiniDSP', 'config_loc')
	logging.debug(f"Setting minidsp-rs binary location to `{G.config.get('MiniDSP', 'bin_loc')}`")
	G.bin_loc = G.config.get('MiniDSP', 'bin_loc')
	logging.debug('Initializing minidsp-rs SDK')
	G.mini = minictl()
	command = f"{G.bin_loc} --config {G.config_loc}"
	try:
		success = can_init(G.candev)
		if not success:
			return 100
		logging.debug('Starting minidsp-rs daemon')
		proc = subprocess.Popen([command])
		logging.debug('Spawning CANBUS command processing thread')
		d = Thread(target=action_thread)
		d.daemon = True
		d.start()
		logging.debug('Starting CANBUS listening loop')
		listen_loop(test)
		if not test:
			logging.error("Listen loop unexpectedly exited")
		logging.debug('Shutting down CANBUS device `{G.candev}`')
		os.system(f'/usr/sbin/ifconfig {G.candev} down')
		logging.debug("Killing minidsp-rs daemon")
		proc.kill()
		return 10
	except KeyboardInterrupt:
		logging.warning("Keyboard interrupt detected, exiting...")
		logging.debug('Shutting down CANBUS device `{G.candev}`')
		os.system(f'/usr/sbin/ifconfig {G.candev} down')
		logging.debug("Killing minidsp-rs daemon")
		proc.kill()
		return 0

G.config = configparser.ConfigParser()
if len(sys.argv) < 2:
	print("Error: no argument specified.")
	print("Valid usage:")
	print("	minican.py {INI_FILE}")
	print("	minican.py {INI_FILE} test")
	sys.exit(1)
else:
	inifile = sys.argv[1]
	G.config.read(inifile)
	# Logging
	if not os.path.isdir(G.config.get('Logging', 'log_dir')):
		os.path.mkdir(G.config.get('Logging', 'log_dir'))
	logging.basicConfig(filename=f"{G.config.get('Logging', 'log_dir')}/minican.log", level=logging.getLevelName(G.config.get('Logging', 'log_level')))
	try:
		testval = sys.argv[2]
		if testval == 'test':
			testval = True
		else:
			testval = False
	except IndexError:
		testval = False
	sys.exit(main(test=testval))
