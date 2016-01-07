#!/usr/bin/python
from optparse import OptionParser
import sys
import os
import shutil
import socket
import threading
import SocketServer
import SimpleHTTPServer
import win32gui, win32con, win32api

DR_RUN_PATH = "./DynamoRIO-Linux-4.1.0-8/bin64/drrun"
BBCOVERAGE_PATH = "./libbbcoverage.so" # path to dynamoRIO coverage lib
HOST, PORT = "localhost", 8081 # host and port for simple http server
TMP = "."
NUL = "/dev/null"

if sys.platform == 'win32':
	DR_RUN_PATH = "..\\DynamoRIO\\bin32\\drrun.exe"
	BBCOVERAGE_PATH = "..\\DynamoRIO\\bbcoverage\\bin\\Release\\bbcoverage.dll" 
	TMP = "%TEMP%"
	NUL = "nul"

if not(os.path.isfile(DR_RUN_PATH)):
	print "File %s not found, check path" % (DR_RUN_PATH)
	exit()
	
if not(os.path.isfile(BBCOVERAGE_PATH)):
	print "File %s not found, check path" % (BBCOVERAGE_PATH)
	exit()
	
def close_window(windowClass, waitTime=15):
	if waitTime > 0:
		threading.Timer(waitTime, close_window, [windowClass, 0]).start()
		return
	hwndqt = win32gui.FindWindow(windowClass, None)
	win32gui.PostMessage(hwndqt,win32con.WM_CLOSE,0,0)	
		
def kill_process(processName, waitTime=15):
	if waitTime > 0:
		threading.Timer(waitTime, kill_process, [processName, 0]).start()
		return
	# command injection? we are running untrusted exe file so idgaf
	os.system("taskkill /F /IM "+processName) 

def readfiles(directory,ext):
	'''
	Recursively go trough the directory tree and enumerate all the files.
	If given, filter by extension. 	
	'''
	files = []
	for dirpath,dirnames,filenames in os.walk(directory):
		for filename in [f for f in filenames]:
			file_path = os.path.join(dirpath,filename)
			if os.path.isfile(file_path):
				if options.extension != None:
					if file_path.find("."+ext) == -1:
						continue
				files.append(file_path)
	return files

# Simple threaded TCP server for handling HTTP connections
class ThreadedTCPServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    pass


class Minblox:
	'''
	Small class containing coverage and minimization methods.
	'''
	def cover(self,app,samples,serve,timeout,logs, force):
		'''
		Method runs the target application trough the sample set with
		under DynamoRIO basic block coverage tool. Coverage logs are 
		saved under logs directory. If serve option is set, serve sample
		files over HTTP server. If timeout is given, stop application
		execution after timeout. 
		'''
		
		#build the command
		cmd = DR_RUN_PATH
		if timeout != None:
			cmd += " -s " + str(timeout)
		cmd += " -logdir " + TMP
		cmd += " -c " + BBCOVERAGE_PATH
		cmd += " -- \"" + app + '"'
		try:
			os.mkdir(logs)
		except:
			pass
		i = 0
		server = None
		if serve: #start the HTTP server if serve option specified
			Handler = SimpleHTTPServer.SimpleHTTPRequestHandler
			server = ThreadedTCPServer((HOST, PORT), Handler)
			server_thread = threading.Thread(target=server.serve_forever)
			server_thread.daemon = True
			server_thread.start()		
		if force or not os.path.isfile(logs + "/base"):
			os.system(cmd + " > " + NUL)
			shutil.move("bbcov.log", logs + "/base")	
		for sample in samples: # instrument application for each sample
			log_path = logs + "/" + sample.replace("/","_").replace("\\", "_").replace(":","_").strip(".")
			if not force and os.path.isfile(log_path):
				print "   ... sample %s already tested, skipping" % sample
				continue
			command = cmd + " "
			if serve:
				command += "http://"+HOST+":"+str(PORT) + "/"
			command += sample
			print "[+] Running trace on sample %s %d out of %d" % (sample, i+1,len(samples))
			i+=1
			os.system(command + " > " + NUL) #don't want app stdout 
			f = open("bbcov.log","a")
			f.write(sample) # record the sample path so we can retrive it later
			f.close()

			shutil.move("bbcov.log",log_path) # save coverage log for analysis
		if server != None:
			server.shutdown()
	
	def find_largest(self,logs):
		'''
		Find log file with most basic blocks covered. 
		That log file is used as a starting point in sample set 
		minimization. Returns file name and set of basic blocks.
		'''
		largest = ""
		most_blocks = set() # use sets so we have unique blocks 
		for log in logs:
			log_file = open(log,"r")
			basic_blocks = set(log_file.readlines()[:-1]) # ignore last line
			log_file.close()
			if len(basic_blocks) > len(most_blocks):
				largest = log
				most_blocks = basic_blocks
		return largest,most_blocks
		
	def minimize(self,logs,output):
		'''
		Minimize the sample set by analyzing the coverage logs.
		Copy minimal sample set to output directory.
		'''
		try:
			os.mkdir(output)
		except:
			pass
		min_files = []
		largest,min_list_bb = self.find_largest(logs) #find starting sample
		print "Biggest coverage achieved in %s with %d basic blocks"%(largest,len(min_list_bb))
		min_files.append(largest)
		for log in logs:
			if log == largest: # don't process the largest one again
				continue
			log_file = open(log,"r")
			log_bb = set(log_file.readlines()[:-1]) # skip last line 
			log_file.close()
			if not log_bb.issubset(min_list_bb): # if it's true subset, it has no new basic blocks
				size_before = len(min_list_bb)
				min_list_bb.update(log_bb)
				min_files.append(log)
				print "Added %d basic blocks to the superset from %s"%(len(min_list_bb)-size_before,log)
		print "Copying minimal set of %d samples to %s."%(len(min_files),output)
		for log in min_files: 
			log_file = open(log,"r")
			file_path = log_file.readlines()[-1].strip()
			shutil.copy(file_path, output + "/" + os.path.basename(file_path))
		print "Done!"
		

print "\n\tMinblox - sample set minimizer\n"


parser = OptionParser()

parser.add_option("-s", "--samples",action="store",
				                    dest="samples",
				                    help="Directory containing file samples. Required with --cover.")
parser.add_option("-a", "--application",action="store",
				                    dest="application",
				                    help="Path to the application to cover. Required with --cover.")
parser.add_option("-S", "--server", action="store_true",
								    dest="http",
								    help="Serve files for coverage over HTTP server .")
parser.add_option("-t", "--timeout", action="store",
								    dest="timeout",
								    type="int",
								    help="Kill application after timeout in seconds.")
parser.add_option("-l", "--logs", action="store",
								    dest="logs",
								    help="Directory containing coverage log files. Required with --minimize and --cover.")
parser.add_option("-e", "--extension", action="store",
									dest="extension",
									help="Filter samples by extension.")		
parser.add_option("-o","--output",action="store",
									dest="output",
									help="Minimal sample set destination directory.")
parser.add_option("-c", "--cover", action="store_true", dest="cover")
parser.add_option("-m", "--minimize", action="store_true", dest="minimize")
parser.add_option("-f", "--force", action="store_true", dest="force", help="Overwrite output even if it exists")



(options, args) = parser.parse_args()
minblox = Minblox()

if (options.cover != None or options.minimize != None) == False:
	print "Choose either coverage or minimization!\n"
	parser.print_help()
	sys.exit(0)

if options.cover and options.minimize:
	print "\tChoose either coverage or minimization\n"
	parser.print_help()
	sys.exit(0)
samples = []	
if options.cover:
	if options.samples == None or options.logs == None:
		print "\n\t-c requires samples directory (-s) and logging directory (-l)"
		sys.exit(0)
	else:
		samples = readfiles(options.samples,options.extension)
		print "[+] Running basic block tracing on %d files"%(len(samples))
		minblox.cover(options.application, samples, options.http , options.timeout,options.logs, options.force)
		sys.exit(0)
logs = []
if options.minimize and (options.logs == None or options.output == None):
	print "\n\y-m requires both logs directory (-l) and output directory (-o)"
else:
	print "RUNNING MINIMIZATION"
	logs = readfiles(options.logs,None)
	minblox.minimize(logs,options.output)
	sys.exit(0)



