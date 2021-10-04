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
	notify_level = None
	notify_attenuate = None
	veh_off_wait = None
	can_bitrate = None
	canint = None
	reverse = None
	q = Queue()
	mini = None
	config_loc = None
	bin_loc = None
	volume = 0
	procs = {
		'Proximity': [None, None],
		'Traffic': [None, None],
		'Beep': [None, None]
	}
	trafwarn = {
		'LEFT': 0,
		'RIGHT': 0
	}
	proxwarn = {
		'LEFT': 0,
		'RIGHT': 0,
		'CENTER': 0
	}

# Consts
VEH_POWER_OFF = 0
VEH_POWER_ON = 2
MUTE_ON = 1
MUTE_OFF = 0
NO_OP = "NoOp"
HU_VOLUME_STATUS = "HU_VolumeStatus"
HU_VEHICLE_POWER = "HU_VehiclePwr"
HU_MUTE_STATUS = "HU_MuteStatus"
REVERSE = "C_InhibitR"
PROX = {
	'FRONT': {
		'LEFT': "Pas_Spkr_Flh_Alarm",
		'RIGHT': "Pas_Spkr_Frh_Alarm",
		'CENTER': "Pas_Spkr_Fcnt_Alarm"
	},
	'REAR': {
		'LEFT': "Pas_Spkr_Rlh_Alarm",
		'RIGHT': "Pas_Spkr_Rrh_Alarm",
		'CENTER': "Pas_Spkr_Rcnt_Alarm"
	}
}
TRAF = {
	'FRONT':{
		'LEFT': "FL_SndWarn",
		'RIGHT': "FR_SndWarn"
	},
	'REAR': {
		'LEFT': "RL_SndWarn",
		'RIGHT': "RR_SndWarn"
	}
}
BEEP = "AMP_DefaultBeep1"
INPUT = 'input'
OUTPUT = 'output'
MASTER = 'master'
MINVOL = -127.0
MAXVOL = 0

def stop_aud(audtype, channel='CENTER', mute=True):
	if G.procs[audtype][1] != None:
		G.procs[audtype][1].kill()
		G.procs[audtype][1] = None
	if G.procs[audtype][0] != None:
		G.procs[audtype][0].kill()
		G.procs[audtype][0] = None
	if mute:
		input = []
		if channel == 'CENTER':
			input.append(G.left_notify_input)
			input.append(G.right_notify_input)
		elif channel == 'LEFT':
			input.append(G.left_notify_input)
		elif channel == 'RIGHT':
			input.append(G.right_notify_input)
		mute_chan(input=input)

def play_aud(audtype='Beep', channel='CENTER', level=1):
	stopproc = True
	attenuate = True
	if audtype == 'Beep':
		fname = f"{G.config.get('Sounds', audtype)}.flac"
		stopproc = False
		attenuate = False
	elif audtype == 'Proximity':
		fname = f"{G.config.get('Sounds', audtype)}{level}-{channel}.flac"
	elif audtype == 'Traffic':
		fname = f"{G.config.get('Sounds', audtype)}{channel}.flac"
	if stopproc:
		stop_aud(audtype, channel=channel, mute=False)
	p1args = [
		G.config.get('Sounds', 'flacloc'),
		'-c',
		'-d',
		fname
	]
	p2args = [
		G.config.get('Sounds', 'aplayloc')
	]
	G.procs[audtype][0] = subprocess.Popen(
		p1args,
		stdout = subprocess.PIPE,
		shell=False
	)
	G.procs[audtype][1] = subprocess.Popen(
		p2args,
		stdin = G.procs[audtype][0].stdout,
		stdout = subprocess.PIPE,
		shell=False
	)


def volume_level(level: int, max_level: int) -> float:
	levperc = level/max_level
	vrange = abs(MINVOL)
	reduction = vrange * levperc
	return reduction + MINVOL


def set_vol(inttype=INPUT, level=0.0, input=[1]):
	try:
		if inttype == MASTER:
			G.mini.mainvolctl(level=level)
		elif inttype == INPUT:
			for row in input:
				G.mini.inputvolctl(level=level, input=row)
		G.mini.submit()
	except Exception as e:
		print(f"Couldn't set volume for {inttype} input {input}: {e}")
	return

def mute_chan(inttype=INPUT, status=True, input=[1]):
	try:
		if inttype == MASTER:
			G.mini.mutemaster(status=status)
		elif inttype == INPUT:
			for row in input:
				G.mini.muteinput(status=status, input=row)
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
	G.volume = 0
	mute = 0
	vpower = 9
	G.reverse = 0
	G.trafwarn = {
		'LEFT': 0,
		'RIGHT': 0,
	}
	G.proxwarn = {
		'LEFT': {'level': 0, 'count': 0},
		'RIGHT': {'level': 0, 'count': 0},
		'CENTER': {'level': 0, 'count': 0}
	}
	proxlevel = {
		"Pas_Spkr_Flh_Alarm": 0,
		"Pas_Spkr_Frh_Alarm": 0,
		"Pas_Spkr_Fcnt_Alarm": 0,
		"Pas_Spkr_Rlh_Alarm": 0,
		"Pas_Spkr_Rrh_Alarm": 0,
		"Pas_Spkr_Rcnt_Alarm": 0
	}
	set_vol(level=G.notify_level, input=[G.left_notify_input, G.right_notify_input])
	mute_chan(input=[G.left_notify_input, G.right_notify_input])
	vtimer = datetime.now()
	while True:
		data = G.q.get()
		if data is None:
			sleep(.05)
		else:
			validcmd = False
			if NO_OP in data:
				validcmd = True
			for channels in PROX.values():
				for channel, cmd in channels.items():
					if cmd in data:
						validcmd = True
						changed = False
						if proxlevel[cmd] != data[cmd]:
							logging.debug(f"Proximty alert command received: `{cmd}`, change from {proxlevel[cmd]} to {data[cmd]}, checking if alert change needed")
							changed = True
							proxlevel[cmd] = data[cmd]
							if G.proxwarn[channel]['level'] < data[cmd]:
								# Issue proximity alert
								if max(int(d['level']) for d in G.proxwarn.values()) < data[cmd]:
									# Higher level prox warning received
									logging.info(f"Issuing proximity alert, {channel} channel, distance level {data[cmd]}")
									play_aud(
										audtype='Proximity',
										channel=channel,
										level=data[cmd]
									)
									G.proxwarn[channel]['level'] = data[cmd]
						if max(int(d) for d in proxlevel.values()) == 0 and changed:
							G.proxwarn['LEFT']['level'] = 0
							G.proxwarn['RIGHT']['level'] = 0
							G.proxwarn['CENTER']['level'] = 0
							# Rescind proximity alert
							logging.info(f"Rescinding all proximity alerts")
							stop_aud('Proximity')						
			for channels in TRAF.values():
				for channel, cmd in channels.items():
					if cmd in data:
						validcmd = True
						if G.trafwarn[channel] == 0 and data[cmd] != 0:
							# Issue proximity alert
							logging.info(f"Issuing traffic alert for location {channel} channel")
							play_aud(
								audtype='Traffic',
								channel=channel,
							)
							G.trafwarn[channel] = data[cmd]
						elif G.trafwarn[channel] != 0 and data[cmd] == 0:
							# Rescind proximity alert
							logging.info(f"Rescinding traffic alert for location channel {channel}")
							stop_aud('Traffic')
							G.trafwarn[channel] = data[cmd]
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
			if BEEP in data:
				validcmd = True
				if data[BEEP] == 1:
					logging.info("Playing BEEP tone")
					play_aud()
			if REVERSE in data:
				validcmd = True
				if data[REVERSE] == 1 and G.reverse != 1:
					# We went into reverse, adjust volume
					logging.info(f'Vehicle in reverse, setting volume to `{G.notify_attenuate}`')
					set_vol(
						input=[
							G.left_main_input,
							G.right_main_input
						],
						level=volume_level(
							G.notify_attenuate,
							G.max_vol
						)
					)
					G.reverse = data[REVERSE]
				elif data[REVERSE] != 1 and G.reverse == 1:
					# No longer in reverse
					logging.info(f"Vehicle no longer in reverse, reverting volume to {G.volume}")
					data[HU_VOLUME_STATUS] = G.volume
					G.volume = 0
					G.reverse = data[REVERSE]
			if HU_VOLUME_STATUS in data:
				validcmd = True
				if int(data[HU_VOLUME_STATUS]) != G.volume:
					logging.info(f"Volume changed from {G.volume} to {data[HU_VOLUME_STATUS]}")
					set_vol(
						input=[
							G.left_main_input,
							G.right_main_input
						],
						level=volume_level(
							int(
								data[HU_VOLUME_STATUS]
							),
							G.max_vol
						)
					)
					G.volume = data[HU_VOLUME_STATUS]
			if HU_MUTE_STATUS in data:
				validcmd = True
				if int(data[HU_MUTE_STATUS]) != mute:
					logging.info(f"Mute changed from {mute} to {data[HU_MUTE_STATUS]}")
					# Set to previous volume level
					if int(data[HU_MUTE_STATUS]) == MUTE_OFF:
						set_vol(
							input=[
								G.left_main_input,
								G.right_main_input
							],
							level=volume_level(
								G.volume,
								G.max_vol
							)
						)
						mute_chan(
							status=False,
							input = [
								G.right_main_input,
								G.left_main_input
							]
						)
					else:
						mute_chan(
							input = [
								G.right_main_input,
								G.left_main_input
							]
						)
						set_vol(
							input=[
								G.left_main_input,
								G.right_main_input
							],
							level=volume_level(
								0,
								G.max_vol
							)
						)
					mute = int(data[HU_MUTE_STATUS])
			if not validcmd:
				pass
				#logging.debug(f"Command not usable, skipping processing: `{pformat(data)}`")

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
			#logging.debug(f"CANBUS message received with the following data: `{pformat(data)}`")
			G.q.put(data)
		except (KeyError, AttributeError):
			pass
			#logging.debug(f"Unknown CANBUS message received: `{pformat(msg)}`")
		if msg is None:
			#logging.debug("No CANBUS message received, sending `NO_OP` to queue")
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
	logging.debug(f"Setting notification input level to `{G.config.get('MiniDSP', 'notify_level')}`")
	G.notify_level = int(G.config.get('MiniDSP', 'notify_level'))
	logging.debug(f"Setting main input attenauation level on notification to `{G.config.get('MiniDSP', 'notify_attenuate')}`")
	G.notify_attenuate = int(G.config.get('MiniDSP', 'notify_attenuate'))
	logging.debug(f"Setting minidsp-rs config location to `{G.config.get('MiniDSP', 'config_loc')}`")
	G.config_loc = G.config.get('MiniDSP', 'config_loc')
	logging.debug(f"Setting minidsp-rs binary location to `{G.config.get('MiniDSP', 'bin_loc')}`")
	G.bin_loc = G.config.get('MiniDSP', 'bin_loc')
	logging.debug('Initializing minidsp-rs SDK')
	testmode = G.config.getboolean('MiniDSP', 'testmode')
	G.mini = minictl(testmode=testmode)
	command = [G.bin_loc, '--config', G.config_loc]
	try:
		success = can_init(G.candev)
		if not success:
			return 100
		logging.debug('Starting minidsp-rs daemon')
		proc = subprocess.Popen(command)
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
		os.mkdir(G.config.get('Logging', 'log_dir'))
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
