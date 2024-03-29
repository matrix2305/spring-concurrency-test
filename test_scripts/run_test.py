import logging
import os
import os.path
import re
import signal
import subprocess
import time
import copy
from datetime import datetime

test_duration = 60
primer_duration = 2
wait_after_primer = 1
wait_to_start = 10
wait_after_kill = 2
now = datetime.now()
javacmd = 'java -Xmx2g -Xms2g'
wrkcmd = 'wrk'
wrktimeout = '20s'

datestring = now.strftime("%Y%m%d_%H%M%S")
resultsfile = 'results_' + datestring + '.log'
outputfile = 'output_' + datestring + '.log'

logger = logging.getLogger('run_test')
logger.setLevel(logging.DEBUG)
# create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
fh = logging.FileHandler(outputfile)
fh.setLevel(logging.DEBUG)
# create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# add formatter to ch
ch.setFormatter(formatter)
fh.setFormatter(formatter)

# add ch to logger
logger.addHandler(ch)
logger.addHandler(fh)

cpuset_conf1 = ['3,5,7,9']
cpuset_conf2 = ['2,4,6,8']
concurrency_conf = ['4','50','100','150','200','250','300','350','400','450','500','600','700','800','900','1000']

# JAR files to test with
jarfiles = [     {'filename': '../test_apps/sb_jpa_hikari_jdbc-0.0.1-SNAPSHOT.jar',
                  'description': 'Web MVC JDBC',
                  'asyncservice': 'no',
                  'asyncdriver': 'no'},
                 {'filename': '../test_apps/sb_webflux_r2dbcpool_r2dbc-0.0.1-SNAPSHOT.jar',
                  'description': 'WebFlux R2DBC',
                  'asyncservice': 'yes',
                  'asyncdriver': 'yes'},
                 {'filename': '../test_apps/sb_jpa_r2dbcpool_r2dbc-0.0.1-SNAPSHOT.jar',
                  'description': 'Web MVC R2DBC',
                  'asyncservice': 'no',
                  'asyncdriver': 'yes'},
                 {'filename': '../test_apps/sb_webflux_jpa_hikari_jdbc-0.0.1-SNAPSHOT.jar',
                  'description': 'WebFlux JDBC',
                  'asyncservice': 'yes',
                  'asyncdriver': 'no'}]

def check_prereqs():
    resval = True;
    for jarfile in jarfiles:
        if not os.path.isfile(jarfile.get('filename')):
            print('File not found: ' + jarfile.get('filename'))
            resval = False
    return resval

def build_jvmcmd(jar):
    return javacmd + ' ' + '-jar ' + jar


def get_user_cpuusage(pid):
    cmd = 'cat /proc/' + pid + '/stat | cut -d \' \' -f 14'
    output = (subprocess.getoutput(cmd)).replace(' ', ',')
    return output


def get_kern_cpuusage(pid):
    cmd = 'cat /proc/' + pid + '/stat | cut -d \' \' -f 15'
    output = (subprocess.getoutput(cmd)).replace(' ', ',')
    return output


def get_contextswitches(pid):
    cmd = 'grep ctxt /proc/' + pid + '/status | awk \'{ print $2 }\' | awk \'{s+=$1} END {print s}\''
    output = (subprocess.getoutput(cmd)).replace(' ', ',')
    return ',' + output


def get_mem_kb_pss(pid):
    cmd = 'cat /proc/' + pid + '/smaps | grep -i pss | awk \'{Total+=$2} END {print Total}\''
    output = (subprocess.getoutput(cmd)).replace(' ', '')
    return ',' + output


def get_mem_kb_rss(pid):
    cmd = 'cat /proc/' + pid + '/smaps | grep -i rss | awk \'{Total+=$2} END {print Total}\''
    output = (subprocess.getoutput(cmd)).replace(' ', '')
    return ',' + output


def get_mem_kb_uss(pid):
    cmd = 'cat /proc/' + pid + '/smaps | grep -i Private | awk \'{Total+=$2} END {print Total}\''
    output = (subprocess.getoutput(cmd)).replace(' ', '')
    return ',' + output


# Estimate test duration
def estimate_duration():
    total = 0
    for jarfile in jarfiles:
        for cpuset1 in cpuset_conf1:
            for cpuset2 in cpuset_conf2:
                for concurrency in concurrency_conf:
                    total = total + test_duration + primer_duration + wait_after_primer + wait_to_start
    return total / 60 / 60


# counts from a comma separated list the number of cpus
def get_cpu_num(cpuset):
    return len(cpuset.split(','))


def exec_all_tests():
    logger.info('Estimated duration: ' + str(estimate_duration()) + ' hours')
    logger.info('Using logfile: ' + resultsfile)
    # write header
    with open(resultsfile, 'a') as the_file:
        the_file.write(
            'description,asyncservice,asyncdriver,cpus_load,cpus_service,concurrency,lat_avg,lat_stdev,lat_max,req_avg,req_stdev,req_max,tot_requests,tot_duration,read,err_connect,err_read,err_write,err_timeout,req_sec_tot,read_tot,user_cpu,kern_cpu,mem_kb_uss,mem_kb_pss,mem_kb_rss,duration\n')
    for jarfile in jarfiles:
        jvmcmd = build_jvmcmd(jarfile.get('filename'));
        logger.info('Processing command: ' + jvmcmd)
        for cpuset_load in cpuset_conf1:
            cpunum_load = str(get_cpu_num(cpuset_load))
            logger.info('Number of CPUs for load generation ' + cpunum_load)
            concurrency_local = []
            for concurrency in concurrency_conf:
                if int(concurrency) >= int(cpunum_load):
                    concurrency_local.append(concurrency)
                else:
                    logger.debug(
                        'Concurrency ' + concurrency + ' < ' + cpunum_load + ' (threads/cores). wrk does not support this. ignoring this concurrency setting')
            for cpuset_service in cpuset_conf2:
                cpunum_service = str(get_cpu_num(cpuset_service))
                logger.info('Number of CPUs for service ' + cpunum_service)
                # concurrency_local is a selection of all the concurrency options. concurrency has to be >= threads/cores for wrk
                for concurrency in concurrency_local:
                    jvm_outputline = jarfile.get('description') + ',' + jarfile.get('asyncservice') + ',' + jarfile.get('asyncdriver') + ',' + cpunum_load + ',' + cpunum_service + ',' + concurrency
                    logger.info('Number of concurrent requests ' + concurrency)

                    pid = start_java_process(jvmcmd, cpuset_service)
                    logger.info('Java process PID is: ' + pid)
                    if (len(str(pid)) == 0):
                        pid = start_java_process(jvmcmd, cpuset_service)
                        logger.warning('Retry startup. Java process PID is: ' + pid)
                        if (len(str(pid)) == 0):
                            pid = start_java_process(jvmcmd, cpuset_service)
                            logger.warning('Second retry startup. Java process PID is: ' + pid)
                    if (len(str(pid)) == 0 and len(str(get_java_process_pid())) > 0):
                        pid = get_java_process_pid()
                        logger.info('Setting new PID to ' + pid)
                    try:
                        output_primer = execute_test_single(cpuset_load, cpunum_load, concurrency, primer_duration)
                        time.sleep(wait_after_primer)
                        cpu_user_before=get_user_cpuusage(pid)
                        cpu_kern_before=get_kern_cpuusage(pid)
                        output_test = execute_test_single(cpuset_load, cpunum_load, concurrency, test_duration)
                        cpu_user_after=get_user_cpuusage(pid)
                        cpu_kern_after=get_kern_cpuusage(pid)
                        wrk_output = parse_wrk_output(output_test)
                        logger.debug("wrk_output: " + str(wrk_output))
                        if str(wrk_output.get('read_tot')) == '0.0':
                            raise Exception('No bytes read. Test failed')
                        cpu_and_mem =  ',' + str(int(int(cpu_user_after)-int(cpu_user_before))) + ',' + str(int(int(cpu_kern_after)-int(cpu_kern_before))) + get_mem_kb_uss(pid) + get_mem_kb_pss(pid) + get_mem_kb_rss(pid)
                        logger.info('CPU and memory: ' + cpu_and_mem)
                        outputline = jvm_outputline + wrk_data(wrk_output) + cpu_and_mem
                    except:
                        # Retry
                        logger.warning('Executing retry')
                        time.sleep(wait_to_start)
                        try:
                            output_primer = execute_test_single(cpuset_load, cpunum_load, concurrency, primer_duration)
                            time.sleep(wait_after_primer)
                            cpu_user_before=get_user_cpuusage(pid)
                            cpu_kern_before=get_kern_cpuusage(pid)
                            output_test = execute_test_single(cpuset_load, cpunum_load, concurrency, test_duration)
                            cpu_user_after=get_user_cpuusage(pid)
                            cpu_kern_after=get_kern_cpuusage(pid)
                            wrk_output = parse_wrk_output(output_test)
                            logger.debug("wrk_output: " + str(wrk_output))
                            if str(wrk_output.get('read_tot')) == '0.0':
                                raise Exception('No bytes read. Test failed')
                            cpu_and_mem =  ',' + str(int(int(cpu_user_after)-int(cpu_user_before))) + ',' + str(int(int(cpu_kern_after)-int(cpu_kern_before))) + get_mem_kb_uss(pid) + get_mem_kb_pss(pid) + get_mem_kb_rss(pid)
                            logger.info('CPU and memory: ' + cpu_and_mem)
                            outputline = jvm_outputline + wrk_data(wrk_output) + cpu_and_mem
                        except Exception as inst:
                            logger.warning("Giving up. Test failed. Writing FAILED to results file")
                            logger.error("Error: " + str(inst))
                            outputline = jvm_outputline + wrk_data_failed()
                    outputline = outputline + ',' + str(test_duration)
                    with open(resultsfile, 'a') as the_file:
                        the_file.write(outputline + '\n')
                    kill_process(pid)
    return


def wrk_data(wrk_output):
    return ',' + str(wrk_output.get('lat_avg')) + ',' + str(wrk_output.get('lat_stdev')) + ',' + str(wrk_output.get(
        'lat_max')) + ',' + str(wrk_output.get('req_avg')) + ',' + str(wrk_output.get('req_stdev')) + ',' + str(
        wrk_output.get(
            'req_max')) + ',' + str(wrk_output.get('tot_requests')) + ',' + str(
        wrk_output.get('tot_duration')) + ',' + str(wrk_output.get(
        'read')) + ',' + str(wrk_output.get('err_connect')) + ',' + str(wrk_output.get('err_read')) + ',' + str(
        wrk_output.get('err_write')) + ',' + str(wrk_output.get('err_timeout')) + ',' + str(
        wrk_output.get('req_sec_tot')) + ',' + str(wrk_output.get('read_tot'))


def wrk_data_failed():
    return ',FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED,FAILED';


def get_bytes(size_str):
    x = re.search("^(\d+\.*\d*)(\w+)$", size_str)
    if x is not None:
        size = float(x.group(1))
        suffix = (x.group(2)).lower()
    else:
        return size_str

    if suffix == 'b':
        return size
    elif suffix == 'kb' or suffix == 'kib':
        return size * 1024
    elif suffix == 'mb' or suffix == 'mib':
        return size * 1024 ** 2
    elif suffix == 'gb' or suffix == 'gib':
        return size * 1024 ** 3
    elif suffix == 'tb' or suffix == 'tib':
        return size * 1024 ** 3
    elif suffix == 'pb' or suffix == 'pib':
        return size * 1024 ** 4

    return False


def get_number(number_str):
    x = re.search("^(\d+\.*\d*)(\w*)$", number_str)
    if x is not None:
        size = float(x.group(1))
        suffix = (x.group(2)).lower()
    else:
        return number_str

    if suffix == 'k':
        return size * 1000
    elif suffix == 'm':
        return size * 1000 ** 2
    elif suffix == 'g':
        return size * 1000 ** 3
    elif suffix == 't':
        return size * 1000 ** 4
    elif suffix == 'p':
        return size * 1000 ** 5
    else:
        return size

    return False


def get_ms(time_str):
    x = re.search("^(\d+\.*\d*)(\w*)$", time_str)
    if x is not None:
        size = float(x.group(1))
        suffix = (x.group(2)).lower()
    else:
        return time_str

    if suffix == 'us':
        return size / 1000
    elif suffix == 'ms':
        return size
    elif suffix == 's':
        return size * 1000
    elif suffix == 'm':
        return size * 1000 * 60
    elif suffix == 'h':
        return size * 1000 * 60 * 60
    else:
        return size

    return False

    #  Thread Stats Avg        Stdev   Max       +/- Stdev
    #    Latency    58.53ms    6.42ms  69.83ms   62.50%
    #    Req/Sec    16.00      5.16    20.00     60.00%
    #  16 requests in 1.00s, 766.79KB read
    #  Socket errors: connect 3076, read 0, write 0, timeout 0
    # Requests/sec:    135.86
    # Transfer/sec:    422.62KB


def parse_wrk_output(wrk_output):
    retval = {}

    for line in wrk_output.splitlines():
        logger.debug("wrk output: " + line)
        x = re.search("^\s+Latency\s+(\d+\.\d+\w*)\s+(\d+\.\d+\w*)\s+(\d+\.\d+\w*).*$", line)
        if x is not None:
            retval['lat_avg'] = get_ms(x.group(1))
            retval['lat_stdev'] = get_ms(x.group(2))
            retval['lat_max'] = get_ms(x.group(3))
        x = re.search("^\s+Req/Sec\s+(\d+\.\d+\w*)\s+(\d+\.\d+\w*)\s+(\d+\.\d+\w*).*$", line)
        if x is not None:
            retval['req_avg'] = get_number(x.group(1))
            retval['req_stdev'] = get_number(x.group(2))
            retval['req_max'] = get_number(x.group(3))
        x = re.search("^\s+(\d+)\ requests in (\d+\.\d+\w*)\,\ (\d+\.\d+\w*)\ read.*$", line)
        if x is not None:
            retval['tot_requests'] = get_number(x.group(1))
            retval['tot_duration'] = get_ms(x.group(2))
            retval['read'] = get_bytes(x.group(3))
        x = re.search("^Requests\/sec\:\s+(\d+\.*\d*).*$", line)
        if x is not None:
            retval['req_sec_tot'] = get_number(x.group(1))
        x = re.search("^Transfer\/sec\:\s+(\d+\.*\d*\w+).*$", line)
        if x is not None:
            retval['read_tot'] = get_bytes(x.group(1))
        x = re.search(
            "^\s+Socket errors:\ connect (\d+\w*)\,\ read (\d+\w*)\,\ write\ (\d+\w*)\,\ timeout\ (\d+\w*).*$", line)
        if x is not None:
            retval['err_connect'] = get_number(x.group(1))
            retval['err_read'] = get_number(x.group(2))
            retval['err_write'] = get_number(x.group(3))
            retval['err_timeout'] = get_number(x.group(4))
    if 'err_connect' not in retval:
        retval['err_connect'] = 0
    if 'err_read' not in retval:
        retval['err_read'] = 0
    if 'err_write' not in retval:
        retval['err_write'] = 0
    if 'err_timeout' not in retval:
        retval['err_timeout'] = 0
    return retval


def get_java_process_pid():
    cmd = 'ps -o pid,sess,cmd afx | egrep "( |/)java.*-SNAPSHOT.*\.jar.*( -f)?$" | awk \'{print $1}\''
    output = subprocess.getoutput(cmd)
    return output


def start_java_process(java_cmd, cpuset):
    oldpid = get_java_process_pid()
    if (oldpid.isdecimal()):
        logger.info('Old Java process found with PID: ' + oldpid + '. Killing it')
        kill_process(oldpid)
    cmd = 'taskset -c ' + cpuset + ' ' + java_cmd
    subprocess.Popen(cmd.split(' '), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    time.sleep(wait_to_start)
    return get_java_process_pid()


def execute_test_single(cpuset, threads, concurrency, duration):
    logger.info('Executing test with concurrency: ' + str(concurrency) + ' and duration ' + str(
        duration) + ' and threads ' + str(threads))
    cmd = 'taskset -c ' + str(cpuset) + ' ' + wrkcmd + ' --timeout ' + wrktimeout + ' -d' + str(
        duration) + 's -c' + str(concurrency) + ' -t' + str(threads) + ' http://localhost:8080/people'
    process = subprocess.run(cmd.split(' '), check=True, stdout=subprocess.PIPE, universal_newlines=True)
    output = process.stdout
    logger.debug('Executing test command ' + cmd)
    logger.info('Executing test done')
    return output


def kill_process(pid):
    logger.info('Killing process with PID: ' + pid)
    try:
        os.kill(int(pid), signal.SIGKILL)
    except:
        logger.info('Process not found')
    try:
        # this will fail if the process is not a childprocess
        os.waitpid(int(pid), 0)
    except:
        # Just to be sure the process is really gone
        time.sleep(wait_after_kill)
    return


def main():
    if (not check_prereqs()):
        logger.error('Prerequisites not satisfied. Exiting')
        exit(1)
    else:
        logger.info('Prerequisites satisfied')
    print(exec_all_tests())
    logger.info('Test execution finished')


if __name__ == '__main__':
    main()
