#!/usr/bin/env python3
"""Mirror two existing VPS snapshots over pinned SSH; no remote mutations or broker calls."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

REMOTE_READ = """import json
from pathlib import Path
paths={'runtime':'/var/lib/docker/volumes/valor-demo_outbox/_data/snapshot.json',
       'experiment':'/var/lib/docker/volumes/valor-experiment_state/_data/snapshot.json'}
values={}
for name,path in paths.items():
 p=Path(path)
 if p.stat().st_size>1000000: raise ValueError('oversized snapshot')
 values[name]=json.loads(p.read_text())
print(json.dumps(values))
"""


def validate(bundle, policy_hash, anchor=None):
    runtime, experiment = bundle['runtime'], bundle['experiment']
    if runtime.get('mode') != 'demo' or experiment.get('mode') != 'virtual_only':
        raise ValueError('unexpected_snapshot_mode')
    if any(value.get('policy_hash') != policy_hash for value in (runtime, experiment)):
        raise ValueError('source_policy_changed')
    if experiment.get('market_source') != 'alpaca':
        raise ValueError('source_change_requires_review')
    identity = experiment['identity_hash']
    if not re.fullmatch('[a-f0-9]{64}', identity):
        raise ValueError('invalid_experiment_identity')
    epoch, end, stamp = (experiment[k] for k in ('epoch', 'evaluation_end', 'timestamp'))
    if not all(isinstance(v, (float, int)) and not isinstance(v, bool) for v in (epoch, end, stamp)):
        raise ValueError('invalid_experiment_clock')
    if not 0 < epoch <= stamp <= time.time()+5 or end != epoch+90*86400:
        raise ValueError('invalid_experiment_clock')
    current = {'identity_hash':identity, 'epoch':epoch, 'evaluation_end':end, 'policy_hash':policy_hash}
    if anchor is not None and current != anchor:
        raise ValueError('experiment_identity_changed')
    return current


def write_private(path, value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,sort_keys=True)
        stream.write('\n')
    os.chmod(temporary,0o600)
    temporary.replace(path)


def mirror(connection, key, destination):
    config=json.loads(connection.read_text())
    # This existing deployment identity can read the experiment volume. The restricted
    # status identity cannot. No key is created, uploaded, embedded or sent to the UI.
    command=['ssh','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
             '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
             '-o','UserKnownHostsFile='+config['known_hosts_path'],
             'root@'+config['host'],'python3 -']
    result=subprocess.run(command,input=REMOTE_READ,text=True,capture_output=True,timeout=25)
    if result.returncode:
        raise RuntimeError('snapshot_transport_failed')
    bundle=json.loads(result.stdout)
    anchor_path=destination/'identity.json'
    anchor=json.loads(anchor_path.read_text()) if anchor_path.exists() else None
    current=validate(bundle,config['policy_hash'],anchor)
    old_path=destination/'experiment.json'
    if old_path.exists() and bundle['experiment']['timestamp'] < json.loads(old_path.read_text())['timestamp']:
        raise ValueError('snapshot_clock_rewound')
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(destination,0o700)
    if anchor is None: write_private(anchor_path,current)
    # Original timestamps and economic fields remain verbatim. Receipt is separate.
    for name,value in bundle.items():
        if name not in ('runtime','experiment'): raise ValueError('unexpected_snapshot')
        write_private(destination/(name+'.json'),value)
    status={'transport':'pinned_ssh','received_at':time.time(),
            'experiment_timestamp':bundle['experiment']['timestamp'], 'frames':bundle['experiment']['frames']}
    write_private(destination/'mirror-status.json',status)
    return status


def main():
    repo=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection',type=Path,default=repo/'.valor/cloud-connection.json')
    parser.add_argument('--key',type=Path,default=repo/'.secrets/valor-study-deploy')
    parser.add_argument('--destination',type=Path,default=repo/'.valor/dashboard')
    parser.add_argument('--interval',type=float,default=10)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if args.interval<5: parser.error('interval must be at least five seconds')
    while True:
        try:
            print(json.dumps({'ok':True,**mirror(args.connection,args.key,args.destination)}),flush=True)
        except (OSError,ValueError,KeyError,RuntimeError,subprocess.TimeoutExpired) as exc:
            # No raw SSH stderr, addresses, connection metadata or secret values in logs.
            print(json.dumps({'ok':False,'error':type(exc).__name__}),flush=True)
            if args.once: return 1
        if args.once: return 0
        time.sleep(args.interval)


if __name__=='__main__':
    raise SystemExit(main())
