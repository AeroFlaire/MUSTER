# Compiler settings
CC = gcc
CFLAGS = -Wall -g

# Libraries required for cryptography and Windows UI
CORE_LIBS = -lbcrypt
GUI_LIBS = -lbcrypt -lgdi32 -luser32 -mwindows

# Default target runs when you just type 'make'
all: app test

# Target 1: Build the main GUI application
app: barcode_scan.c
	$(CC) $(CFLAGS) barcode_scan.c -o scanner.exe $(GUI_LIBS)

# Target 2: Build the console test suite
test: barcode_scan.c barcode_scan_tests.c
	$(CC) $(CFLAGS) barcode_scan_tests.c barcode_scan.c -o tests.exe $(CORE_LIBS)

# Clean up compiled executables
clean:
	del scanner.exe tests.exe