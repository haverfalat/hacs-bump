DOMAIN = 'bump'
PLATFORMS = ['device_tracker', 'sensor', 'binary_sensor']
DEFAULT_STALE_SECONDS = 900


def parse_wake_user_uids(value):
    """Normalize a comma/space/newline-separated list of positive uint64 IDs."""
    import re
    if not isinstance(value, str):
        raise ValueError('User IDs must be text')
    result=[]
    for token in re.split(r'[\s,;]+', value.strip()):
        if not token:continue
        if not token.isascii() or not token.isdigit() or not 0<int(token)<1<<64:
            raise ValueError('Invalid user ID')
        uid=str(int(token))
        if uid not in result:result.append(uid)
    return tuple(result)


def wake_mode(options):
    mode=options.get('wake_mode', 'all' if options.get('wake_friends',True) else 'off')
    return mode if mode in ('all','off','selected') else 'off'
