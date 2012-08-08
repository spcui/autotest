"""
Utility classes and functions to handle connection to a libvirt host system

Suggested usage: import autotest.client.virt.virsh

The entire contents of callables in this module (minus the names defined in
_NOCLOSE below), will become methods of the Virsh and VirshPersistant classes.
A Closure class is used to wrap the module functions, and allow for
Virsh/VrshPersistant instance state to be passed.  This is acomplished
by encoding state as dictionary of keyword arguments, and passing
that to the module functions.

Therefor, all virsh module functions _MUST_ include a '**dargs' (variable
keyword arguments placeholder).  Accessing them is safest by using the
'dargs.get(<name>, <default>)' call.  The keywords present in **dargs
is defined by VIRSH_PROPERTIES and possibly VIRSH_SESSION_PROPS.

@copyright: 2012 Red Hat Inc.
"""

import logging, urlparse
from autotest.client import utils, os_dep
from autotest.client.shared import error, contents
import aexpect, virt_vm


# Needs to be in-scope for Virsh* class screenshot method and module function
_SCREENSHOT_ERROR_COUNT = 0

# list of symbol names NOT to wrap as Virsh class methods
# Everything else from globals() will become a method of Virsh class
_NOCLOSE = contents.get_map().keys() + [
    '_SCREENSHOT_ERROR_COUNT', '_NOCLOSE', 'VirshBase', 'DArgMangler',
    'VirshSession', 'Closure', 'Virsh', 'VirshPersistant'
]

# Virsh class properties and default values
# Schema: {<name>:<default>}
VIRSH_PROPERTIES = {
    'uri':"",
    'ignore_status':False,
    'virsh_exec':os_dep.command("virsh"),
    'debug':False,
}

# Persistant session virsh class properties and default values
VIRSH_SESSION_PROPS = {
    'session':None,
    'session_id':None
}

class VirshBase(dict):
    """
    Base Class storing libvirt Connection & state to a host
    """

    # properties only work if set on a class, so hit this from __new__
    @classmethod
    def _generate_property(cls, property_name):
        # Used class's getters/setters/delters if they exist
        getter = getattr(cls, 'get_%s' % property_name,
                         lambda self: getattr(self, '_'+property_name))
        setter = getattr(cls, 'set_%s' % property_name,
                         lambda self,value: setattr(self,
        delter = getattr(cls, 'del_%s' % property_name,
                         lambda self: delattr(self, '_'+property_name))
        # Don't overwrite existing
        if not hasattr(cls, property_name):
            setattr(cls, property_name, property(getter, setter, delter))


    def __new__(cls, **dargs):
        """
        Sets up generic getters/setters/deleteters if not defined for class
        """
        # allow dargs to extend VIRSH_PROPERTIES
        for name in VIRSH_PROPERTIES.copy().update(dargs).keys():
            cls._generate_property(name)
        # Super doesn't work for classes on python 2.4
        return dict.__new__(cls)


    def __init__(self, **dargs):
        """
        Initialize libvirt connection/state from VIRSH_PROPERTIES and/or dargs
        """
        # Setup defaults, (calls properties)
        for name,value in VIRSH_PROPERTIES.copy().update(dargs).items():
            self[name] = value


    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(str(key))


    def __setitem__(self, key, value):
        # Calls property functions if defined
        setattr(self, key, value):


    def __delitem__(self, key):
        try:
            delattr(self, key)
        except AttributeError:
            raise KeyError(str(key))


    def get_ignore_status(self):
        if self._ignore_status:
            return True
        else:
            return False


    def set_ignore_status(self, ignore_status):
        if ignore_status:
            self._ignore_status = True
        else:
            self._ignore_status = False


    def set_debug(self, debug):
        if debug:
            self._debug = True
            logging.debug("Virsh debugging switched on")
        else:
            self._debug = False
            logging.debug("Virsh debugging switched off")


# Ahhhhh, look out!  It's D Arg mangler comnta gettcha!
class DArgMangler(dict):
    """
    Dict-like class that checks parent before raising KeyError
    """

    def __init__(self, parent, *args, **dargs):
        """
        Initialize DargMangler dict-like instance.

        param: parent: Parent instance to pull properties from as needed
        param: *args: passed to ancestors constructor
        param: **dargs: passed to ancestors constructor
        """
        self._parent = parent
        super(DArgMangler, self).__init__(*args, **dargs)


    def __getitem__(self, key):
        """
        Look up key's value on instance, then parent instance attributes

        @param: key: key to look up non-None value
        @raises: KeyError: When value is None, undefined locally or on parent.
        """
        try:
            return super(DArgMangler, self)[key]
        except KeyError:
            # Assume parent raises KeyError if value == None
            return self.parent[key]


class VirshSession(aexpect.ShellSession):
    """
    A virsh shell session, used with Virsh instances.
    """

    # No way to get virsh sub-command "exit" status
    # Check output against list of known error-status strings
    ERROR_REGEX_LIST = ['error:\s*', 'failed']

    def __init__(self, virsh_exec=None, uri=None, id=None,
                 prompt=r"virsh\s*\#\s*"):
        """
        Initialize virsh session server, or client if id set.

        @param: virsh_exec: path to virsh executable
        @param: uri: uri of libvirt instance to connect to
        @param: id: ID of an already running server, if accessing a running
                server, or None if starting a new one.
        @param prompt: Regular expression describing the shell's prompt line.
        """

        uri_arg = ""
        if uri:
            virsh_exec += " -c '%s'" % uri

        super(VirshSession, self).__init__(virsh_exec, id, prompt=prompt)


    def cmd_status_output(self, cmd, timeout=60, internal_timeout=None,
                          print_func=None):
        """
        Send a virsh command and return its exit status and output.

        @param cmd: virsh command to send (must not contain newline characters)
        @param timeout: The duration (in seconds) to wait for the prompt to
                return
        @param internal_timeout: The timeout to pass to read_nonblocking
        @param print_func: A function to be used to print the data being read
                (should take a string parameter)
        @return: A tuple (status, output) where status is the exit status and
                output is the output of cmd
        @raise ShellTimeoutError: Raised if timeout expires
        @raise ShellProcessTerminatedError: Raised if the shell process
                terminates while waiting for output
        @raise ShellStatusError: Raised if the exit status cannot be obtained
        @raise ShellError: Raised if an unknown error occurs
        """

        o = self.cmd_output(cmd, timeout, internal_timeout, print_func)
        for line in o.splitlines():
            if self.match_patterns(line, self.ERROR_REGEX_LIST):
                return 1, o
        return 0, o


    def cmd_result(self, cmd, ignore_status=False):
        """Mimic utils.run()"""
        exit_status, stdout = self.cmd_status_output(cmd)
        stderr = '' # no way to retrieve this separatly
        result = utils.CmdResult(cmd, stdout, stderr, exit_status)
        if not ignore_status and exit_status:
            raise error.CmdError(cmd, result,
                                 "Virsh Command returned non-zero exit status")
        return result

# Work around for inconsistent builtin closure local reference problem
# across different versions of python
class Closure(object):
    """
    Callable instances for function with persistant internal state
    """

    def __init__(self, ref, state):
        """
        Initialize state to call ref with mangled keyword args
        """
        self._state = state
        self._ref = ref


    def __call__(self, *args, **dargs):
        """
        Retrieve keyword args from state before calling ref function
        """
        return _ref(*args, **DArgMangler(self._state, dargs))


class Virsh(VirshBase):
    """
    Execute libvirt operations, using a new virsh shell each time.
    """

    def __init__(self, **dargs):
        """
        Initialize Virsh instance with persistant options

        @param: **dargs: initial values for VIRSH_PROPERTIES
        """
        super(Virsh, self).__init__(**dargs)
        # Define the instance callables from the contents of this module
        # to avoid using class methods and hand-written aliases
        for sym, ref in globals().items():
            if sym not in self._NOCLOSE and callable(ref):
                self[sym] = Closure(ref, self))


class VirshPersistant(Virsh):
    """
    Execute libvirt operations using persistant virsh session.
    """

    def __new__(cls, **dargs):
        # Allow dargs to extend VIRSH_SESSION_PROPS
        _dargs = VIRSH_SESSION_PROPS.copy().update(dargs)
        # python 2.4 can't use super on class objects
        return Virsh.__new__(**_dargs)


    def __init__(self, **dargs):
        """
        Initialize persistant virsh session.

        @param: **dargs: initial values for VIRSH_[PROPERTIES,SESSION_PROPS]
        """

        _dargs = VIRSH_SESSION_PROPS.copy().update(dargs)
        # new_session() called by super via uri property (below)
        super(VirshPersistant, self).__init__(**_dargs)


    def __del__(self):
        if self.get('session_id') and self.get('session'):
            self.get('session').close()


    def new_session(self):
        """
        Close current virsh session and open new.
        """

        if self.get('session_id') and self.get('session'):
            self.session.close()
        self['session'] = VirshSession(self.get('virsh_exec'), self.get('uri'))
        self['session_id'] = self['session'].get_id()


    def set_uri(self, uri):
        """
        Change instances uri, and re-connect virsh shell session.

        Accessed via property, i.e. virsh.uri = 'qemu://foobar/system'
        """
        # Don't assume ancestor get/set_uri() wasn't overridden
        if super(Virsh, self).uri != uri:
            super(Virsh, self).uri = uri
            self.new_session()


##### virsh module functions follow (See module docstring for API) #####


def cmd(cmd, **dargs):
    """
    Interface to cmd function as 'cmd' symbol is polluted

    @param: cmd: Command line to append to virsh command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    @raises: CmdError if non-zero exit status and ignore_status=False
    """

    uri = dargs.get('uri')
    virsh_exec = dargs.get('virsh_exec')
    debug = dargs.get('debug')
    ignore_status = dargs.get('ignore_status')
    session_id = dargs.get('session_id')

    if session_id:
        session = VirshSession(id=session_id)
        ret = session.cmd_result(cmd, ignore_status)
    else:
        uri_arg = " "
        if uri:
            uri_arg = " -c '%s' " % uri

        cmd = "%s%s%s" % (virsh_exec, uri_arg, cmd)
        if debug:
            logging.debug("Running command: %s" % cmd)
        ret = utils.run(cmd, verbose=debug, ignore_status=ignore_status)

    if debug:
        logging.debug("status: %s" % ret.exit_status)
        logging.debug("stdout: %s" % ret.stdout.strip())
        logging.debug("stderr: %s" % ret.stderr.strip())
    return ret


def domname(id, **dargs):
    """
    Convert a domain id or UUID to domain name

    @param id: a domain id or UUID.
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    return cmd("domname --domain %s" % id, **dargs)


def qemu_monitor_command(domname, command, **dargs):
    """
    This helps to execute the qemu monitor command through virsh command.

    @param: domname: Name of monitor domain
    @param: command: monitor command to execute
    @param: dargs: standardized virsh function API keywords
    """

    cmd_qemu_monitor = "qemu-monitor-command %s --hmp \'%s\'" % (domname, command)
    return cmd(cmd_qemu_monitor, **dargs)


def vcpupin(domname, vcpu, cpu, **dargs):
    """
    Changes the cpu affinity for respective vcpu.

    @param: domname: name of domain
    @param: vcpu: virtual CPU to modify
    @param: cpu: physical CPU specification (string)
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """

    try:
        cmd_vcpupin = "vcpupin %s %s %s" % (domname, vcpu, cpu)
        cmd(cmd_vcpupin, **dargs)

    except error.CmdError, detail:
        logging.error("Virsh vcpupin VM %s failed:\n%s", domname, detail)
        return False


def vcpuinfo(domname, **dargs):
    """
    Prints the vcpuinfo of a given domain.

    @param: domname: name of domain
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """

    cmd_vcpuinfo = "vcpuinfo %s" % domname
    return cmd(cmd_vcpuinfo, **dargs).stdout.strip()


def vcpucount_live(domname, **dargs):
    """
    Prints the vcpucount of a given domain.

    @param: domname: name of a domain
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """

    cmd_vcpucount = "vcpucount --live --active %s" % domname
    return cmd(cmd_vcpucount, **dargs).stdout.strip()


def freecell(extra="", **dargs):
    """
    Prints the available amount of memory on the machine or within a NUMA cell.

    @param: dargs: extra: extra argument string to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd_freecell = "freecell %s" % extra
    return cmd(cmd_freecell, **dargs)


def nodeinfo(extra="", **dargs):
    """
    Returns basic information about the node,like number and type of CPU,
    and size of the physical memory.

    @param: dargs: extra: extra argument string to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd_nodeinfo = "nodeinfo %s" % extra
    return cmd(cmd_nodeinfo, **dargs)


def uri(**dargs):
    """
    Return the hypervisor canonical URI.

    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("uri", **dargs).stdout.strip()


def hostname(**dargs):
    """
    Return the hypervisor hostname.

    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("hostname", **dargs).stdout.strip()


def version(**dargs):
    """
    Return the major version info about what this built from.

    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("version", **dargs).stdout.strip()


def driver(**dargs):
    """
    Return the driver by asking libvirt

    @param: dargs: standardized virsh function API keywords
    @return: VM driver name
    """
    # libvirt schme composed of driver + command
    # ref: http://libvirt.org/uri.html
    scheme = urlparse.urlsplit( uri(**dargs) )[0]
    # extract just the driver, whether or not there is a '+'
    return scheme.split('+', 2)[0]


def domstate(name, **dargs):
    """
    Return the state about a running domain.

    @param name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("domstate %s" % name, **dargs).stdout.strip()


def domid(name, **dargs):
    """
    Return VM's ID.

    @param name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("domid %s" % (name), **dargs).stdout.strip()


def dominfo(name, **dargs):
    """
    Return the VM information.

    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("dominfo %s" % (name), **dargs).stdout.strip()


def domuuid(name, **dargs):
    """
    Return the Converted domain name or id to the domain UUID.

    @param name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    return cmd("domuuid %s" % name, **dargs).stdout.strip()


def screenshot(name, filename, **dargs):
    """
    Capture a screenshot of VM's console and store it in file on host

    @param: name: VM name
    @param: filename: name of host file
    @param: dargs: standardized virsh function API keywords
    @return: filename
    """
    try:
        cmd("screenshot %s %s" % (name, filename), **dargs)
    except error.CmdError, detail:
        if _SCREENSHOT_ERROR_COUNT < 1:
            logging.error("Error taking VM %s screenshot. You might have to "
                          "set take_regular_screendumps=no on your "
                          "tests.cfg config file \n%s.  This will be the "
                          "only logged error message.", name, detail)
        _SCREENSHOT_ERROR_COUNT += 1
    return filename


def dumpxml(name, to_file="", **dargs):
    """
    Return the domain information as an XML dump.

    @param: name: VM name
    @param: to_file: optional file to write XML output to
    @param: dargs: standardized virsh function API keywords
    @return: standard output from command
    """
    if to_file:
        cmd = "dumpxml %s > %s" % (name, to_file)
    else:
        cmd = "dumpxml %s" % name

    return cmd(cmd, **dargs).stdout.strip()


def is_alive(name, **dargs):
    """
    Return True if the domain is started/alive.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    return not is_dead(name, **dargs)


def is_dead(name, **dargs):
    """
    Return True if the domain is undefined or not started/dead.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        state = domstate(name, **dargs)
    except error.CmdError:
        return True
    if state in ('running', 'idle', 'no state', 'paused'):
        return False
    else:
        return True


def suspend(name, **dargs):
    """
    True on successful suspend of VM - kept in memory and not scheduled.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        cmd("suspend %s" % (name), **dargs)
        if domstate(name, **dargs) == 'paused':
            logging.debug("Suspended VM %s", name)
            return True
        else:
            return False
    except error.CmdError, detail:
        logging.error("Suspending VM %s failed:\n%s", name, detail)
        return False


def resume(name, **dargs):
    """
    True on successful moving domain out of suspend

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        cmd("resume %s" % (name), **dargs)
        if is_alive(name, **dargs):
            logging.debug("Resumed VM %s", name)
            return True
        else:
            return False
    except error.CmdError, detail:
        logging.error("Resume VM %s failed:\n%s", name, detail)
        return False


def save(name, path, **dargs):
    """
    Store state of VM into named file.

    @param: name: VM Name to operate on
    @param: path: absolute path to state file
    @param: dargs: standardized virsh function API keywords
    """
    state = domstate(name, **dargs)
    if state not in ('paused',):
        raise virt_vm.VMStatusError("Cannot save a VM that is %s" % state)
    logging.debug("Saving VM %s to %s" %(name, path))
    cmd("save %s %s" % (name, path), **dargs)
    # libvirt always stops VM after saving
    state = domstate(name, **dargs)
    if state not in ('shut off',):
        raise virt_vm.VMStatusError("VM not shut off after save")


def restore(name, path, **dargs):
    """
    Load state of VM from named file and remove file.

    @param: name: VM Name to operate on
    @param: path: absolute path to state file.
    @param: dargs: standardized virsh function API keywords
    """
    # Blindly assume named VM cooresponds with state in path
    # rely on higher-layers to take exception if missmatch
    state = domstate(name, **dargs)
    if state not in ('shut off',):
        raise virt_vm.VMStatusError("Can not restore VM that is %s" % state)
    logging.debug("Restoring VM from %s" % path)
    cmd("restore %s" % path, **dargs)
    state = domstate(name, **dargs)
    if state not in ('paused','running'):
        raise virt_vm.VMStatusError("VM not paused after restore, it is %s." %
                state)


def start(name, **dargs):
    """
    True on successful start of (previously defined) inactive domain.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    if is_alive(name, **dargs):
        return True
    try:
        cmd("start %s" % (name), **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Start VM %s failed:\n%s", name, detail)
        return False


def shutdown(name, **dargs):
    """
    True on successful domain shutdown.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    if domstate(name, **dargs) == 'shut off':
        return True
    try:
        cmd("shutdown %s" % (name), **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Shutdown VM %s failed:\n%s", name, detail)
        return False


def destroy(name, **dargs):
    """
    True on successful domain destruction

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    if domstate(name, **dargs) == 'shut off':
        return True
    try:
        cmd("destroy %s" % (name), **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Destroy VM %s failed:\n%s", name, detail)
        return False


def define(xml_path, **dargs):
    """
    Return True on successful domain define.

    @param: xml_path: XML file path
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        cmd("define --file %s" % xml_path, **dargs)
        return True
    except error.CmdError:
        logging.error("Define %s failed.", xml_path)
        return False


def undefine(name, **dargs):
    """
    Return True on successful domain undefine (after sutdown/destroy).

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        cmd("undefine %s" % (name), **dargs)
        logging.debug("undefined VM %s", name)
        return True
    except error.CmdError, detail:
        logging.error("undefine VM %s failed:\n%s", name, detail)
        return False


def remove_domain(name, **dargs):
    """
    Return True after forcefully removing a domain if it exists.

    @param: name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    if domain_exists(name, **dargs):
        if is_alive(name, **dargs):
            destroy(name, **dargs)
        undefine(name, **dargs)
    return True


def domain_exists(name, **dargs):
    """
    Return True if a domain exits.

    @param name: VM name
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    try:
        cmd("domstate %s" % name, **dargs)
        return True
    except error.CmdError, detail:
        logging.warning("VM %s does not exist:\n%s", name, detail)
        return False


def migrate(name="", dest_uri="", option="", extra="", **dargs):
    """
    Migrate a guest to another host.

    @param: name: name of guest on uri.
    @param: dest_uri: libvirt uri to send guest to
    @param: option: Free-form string of options to virsh migrate
    @param: extra: Free-form string of options to follow <domain> <desturi>
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "migrate"
    if option:
        cmd += " %s" % option
    if name:
        cmd += " --domain %s" % name
    if dest_uri:
        cmd += " --desturi %s" % dest_uri
    if extra:
        cmd += " %s" % extra

    return cmd(cmd, **dargs)


def attach_device(name, xml_file, extra="", **dargs):
    """
    Attach a device to VM.

    @param: name: name of guest
    @param: xml_file: xml describing device to detach
    @param: extra: additional arguments to command
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    cmd = "attach-device --domain %s --file %s %s" % (name, xml_file, extra)
    try:
        cmd(cmd, **dargs)
        return True
    except error.CmdError:
        logging.error("Attaching device to VM %s failed." % name)
        return False


def detach_device(name, xml_file, extra="", **dargs):
    """
    Detach a device from VM.

    @param: name: name of guest
    @param: xml_file: xml describing device to detach
    @param: extra: additional arguments to command
    @param: dargs: standardized virsh function API keywords
    @return: True operation was successful
    """
    cmd = "detach-device --domain %s --file %s %s" % (name, xml_file, extra)
    try:
        cmd(cmd, **dargs)
        return True
    except error.CmdError:
        logging.error("Detaching device from VM %s failed." % name)
        return False


def attach_interface(name, option="", **dargs):
    """
    Attach a NIC to VM.

    @param: name: name of guest
    @param: option: options to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "attach-interface "

    if name:
        cmd += "--domain %s" % name
    if option:
        cmd += " %s" % option

    return cmd(cmd, **dargs)


def detach_interface(name, option="", **dargs):
    """
    Detach a NIC to VM.

    @param: name: name of guest
    @param: option: options to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "detach-interface "

    if name:
        cmd += "--domain %s" % name
    if option:
        cmd += " %s" % option

    return cmd(cmd, **dargs)


def net_create(xml_file, extra="", **dargs):
    """
    Create network from a XML file.

    @param: xml_file: xml defining network
    @param: extra: extra parameters to pass to command
    @param: options: options to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "net-create --file %s %s" % (xml_file, extra)
    return cmd(cmd, **dargs)


def net_list(options, extra="", **dargs):
    """
    List networks on host.

    @param: extra: extra parameters to pass to command
    @param: options: options to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "net-list %s %s" % (options, extra)
    return cmd(cmd, **dargs)


def net_destroy(name, extra="", **dargs):
    """
    Destroy actived network on host.

    @param: name: name of guest
    @param: extra: extra string to pass to command
    @param: dargs: standardized virsh function API keywords
    @return: CmdResult object
    """
    cmd = "net-destroy --network %s %s" % (name, extra)
    return cmd(cmd, **dargs)


def pool_info(name, **dargs):
    """
    Returns basic information about the storage pool.

    @param: name: name of pool
    @param: dargs: standardized virsh function API keywords
    """
    cmd = "pool-info %s" % name
    try:
        cmd(cmd, **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Pool %s doesn't exist:\n%s", name, detail)
        return False


def pool_destroy(name, **dargs):
    """
    Forcefully stop a given pool.

    @param: name: name of pool
    @param: dargs: standardized virsh function API keywords
    """
    cmd = "pool-destroy %s" % name
    try:
        cmd(cmd, **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Failed to destroy pool: %s." % detail)
        return False


def pool_create_as(name, pool_type, target, extra="", **dargs):
    """
    Create a pool from a set of args.

    @param: name: name of pool
    @param: pool_type: storage pool type such as 'dir'
    @param: target: libvirt uri to send guest to
    @param: extra: Free-form string of options
    @param: dargs: standardized virsh function API keywords
    @return: True if pool creation command was successful
    """

    if not name:
        logging.error("Please give a pool name")

    types = [ 'dir', 'fs', 'netfs', 'disk', 'iscsi', 'logical' ]

    if pool_type and pool_type not in types:
        logging.error("Only support pool types: %s." % types)
    elif not pool_type:
        pool_type = types[0]

    logging.info("Create %s type pool %s" % (pool_type, name))
    cmd = "pool-create-as --name %s --type %s --target %s %s" \
          % (name, pool_type, target, extra)
    try:
        cmd(cmd, **dargs)
        return True
    except error.CmdError, detail:
        logging.error("Failed to create pool: %s." % detail)
        return False