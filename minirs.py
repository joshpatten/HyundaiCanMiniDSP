import requests
from pprint import pprint

class minictl():

    def __init__(self, http_addr='127.0.0.1', http_port=5380, device=0):
        self.http_addr = http_addr
        self.http_port = http_port
        self.device = device
        self.payload = {}
        self.url = f"http://{self.http_addr}:{self.http_port}/devices/{self.device}"

    def query(self):
        r = requests.get(self.url)
        if r.status_code != requests.codes.ok:
            print(f"ERR: received status code: {r.status_code}")
            return {}
        return r.json()

    def submit(self):
        r = requests.post(f"{self.url}/config", json=self.payload)
        if r.status_code != requests.codes.ok:
            print(f"ERR: received status code: {r.status_code}")
            print("Payload that failed:")
            pprint(self.payload, indent=4)
        return
        self.payload = {}

    def mainvolctl(self, level=-127.0):
        if 'master_status' not in self.payload:
            self.payload['master_status'] = {}
        self.payload['master_status']['volume'] = level
        return

    def inputvolctl(self, level=-100.0, input=1):
        if 'inputs' not in self.payload:
            self.payload['inputs'] = []
        ndex = None
        i = 0
        for row in self.payload['inputs']:
            if row['index'] == input:
                index = i
                break
            i += 1
        if index:
            self.payload['inputs'][index]['gain'] = level
        else:
            self.payload['inputs'].append(
                {
                    'gain': level,
                    'index': input
                }
            )
        return

    def mutemaster(self, status=True):
        if 'master_status' not in self.payload:
            self.payload['master_status'] = {}
        self.payload['master_status']['mute'] = status
        return

    def muteinput(self, status=True, input=1):
        if 'inputs' not in self.payload:
            self.payload['inputs'] = []
        index = None
        i = 0
        for row in self.payload['inputs']:
            if row['index'] == input:
                index = i
                break
            i += 1
        if index:
            self.payload['inputs'][index]['mute'] = status
        else:
            self.payload['inputs'].append(
                {
                    'mute': status,
                    'index': input
                }
            )
        return

    