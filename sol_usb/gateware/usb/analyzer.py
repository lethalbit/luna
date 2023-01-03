# SPDX-License-Identifier: BSD-3-Clause
#
# This file is part of SOL.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>

''' Low-level USB analyzer gateware. '''



from torii          import Elaboratable, Module, Signal, DomainRenamer
from torii.lib.fifo import SyncFIFOBuffered

from ..stream       import StreamInterface


class USBAnalyzer(Elaboratable):
	'''
	Core USB analyzer; backed by a small ringbuffer in FPGA block RAM.

	If you're looking to instantiate a full analyzer, you'll probably want to grab
	one of the DRAM-based ringbuffer variants (which are currently forthcoming).

	If you're looking to use this with a ULPI PHY, rather than the FPGA-convenient UTMI interface,
	grab the UTMITranslator from `sol.gateware.interface.ulpi`.

	Attributes
	----------
	stream: StreamInterface(), output stream
		Stream that carries USB analyzer data.

	idle: Signal(), output
		Asserted iff the analyzer is not currently receiving data.
	overrun: Signal(), output
		Asserted iff the analyzer has received more data than it can store in its internal buffer.
		Occurs if :attr:``stream`` is not being read quickly enough.
	capturing: Signal(), output
		Asserted iff the analyzer is currently capturing a packet.


	Parameters
	----------
	utmi_interface: UTMIInterface()
		The UTMI interface that carries the data to be analyzed.
	mem_depth: int, default = 8192
		The depth of the analyzer's local ringbuffer, in bytes.
		Must be a power of 2.
	'''

	# Current, we'll provide a packet header of 16 bits.
	HEADER_SIZE_BITS = 16
	HEADER_SIZE_BYTES = HEADER_SIZE_BITS // 8

	# Support a maximum payload size of 1024B, plus a 1-byte PID and a 2-byte CRC16.
	# Please note, this is less than the max actual size of 8192B from the USB spec(!)
	MAX_PACKET_SIZE_BYTES = 1024 + 1 + 2

	def __init__(self, *, utmi_interface, mem_depth = 65536):
		'''
		Parameters
		----------
		utmi_interface
			A record or elaboratable that presents a UTMI interface.

		'''

		self.utmi = utmi_interface

		if (mem_depth % 2) != 0:
			raise ValueError('mem_depth must be a power of 2')

		# Internal storage item count
		self.mem_size = mem_depth

		#
		# I/O port
		#
		self.stream         = StreamInterface()

		self.capture_enable = Signal()
		self.idle           = Signal()
		self.overrun        = Signal()
		self.capturing      = Signal()

		# Diagnostic I/O.
		self.sampling       = Signal()


	def elaborate(self, platform):
		m = Module()

		# Internal storage
		m.submodules.ringbuffer = data_buffer = DomainRenamer('usb')(
			SyncFIFOBuffered(width = 8, depth = self.mem_size)
		)
		m.submodules.packet_buffer = packet_buffer = DomainRenamer('usb')(
			SyncFIFOBuffered(width = 8, depth = USBAnalyzer.MAX_PACKET_SIZE_BYTES)
		)
		m.submodules.length_buffer = length_buffer = DomainRenamer('usb')(
			SyncFIFOBuffered(width = 16, depth = 512)
		)

		# Current receive status.
		captured_packet_length = Signal(16)
		packet_length = Signal(16)
		packet_transferred = Signal(17)

		# Read FIFO logic.
		m.d.comb += [

			# We have data ready whenever there's data in the FIFO.
			self.stream.valid.eq(data_buffer.r_rdy),
			# Our data_out is always the output of our read port...
			self.stream.payload.eq(data_buffer.r_data),
			# Read more data out for as long as the ready signal is asserted
			data_buffer.r_en.eq(self.stream.ready),

			self.sampling.eq(packet_buffer.w_en),

			length_buffer.w_en.eq(0),
		]

		# Core analysis FSM.
		with m.FSM(domain = 'usb', name = 'capture') as fsm:
			m.d.comb += [
				self.idle.eq(fsm.ongoing('IDLE')),
				self.capturing.eq(fsm.ongoing('CAPTURE')),
			]

			# START: wait for capture to be enabled, but don't start mid-packet.
			with m.State('START'):
				with m.If(~self.utmi.rx_active & self.capture_enable):
					m.next = 'IDLE'

			# IDLE: If capture is enabled, wait for an active receive.
			with m.State('IDLE'):

				# If capture is disabled, stall and return to the wait state for starting a new capture
				with m.If(~self.capture_enable):
					m.next = 'START'
				# We got a new active receive, capture it
				with m.Elif(self.utmi.rx_active):
					m.d.usb += captured_packet_length.eq(0)
					m.next = 'CAPTURE'

			# Capture data until the packet is complete.
			with m.State('CAPTURE'):

				byte_received = self.utmi.rx_valid & self.utmi.rx_active

				# Capture data whenever rx_valid is asserted.
				m.d.comb += [
					packet_buffer.w_data.eq(self.utmi.rx_data),
					packet_buffer.w_en.eq(byte_received),
				]

				# Add to the packet size every time we receive a byte.
				with m.If(byte_received):
					m.d.usb += captured_packet_length.eq(captured_packet_length + 1)

				# If we've stopped receiving, go back to idle to wait for more.
				with m.If(~self.utmi.rx_active):
					m.d.comb += [
						length_buffer.w_data.eq(captured_packet_length),
						length_buffer.w_en.eq(1),
					]
					m.next = 'IDLE'

		with m.FSM(domain = 'usb', name = 'packet_queue'):
			# IDLE: When there are no packets ready for processing wait in this state.
			with m.State('IDLE'):
				with m.If(length_buffer.r_rdy):
					m.next = 'POP_LENGTH'

			# POP_LENGTH: Grab the new packet length
			with m.State('POP_LENGTH'):
				m.d.usb += packet_length.eq(length_buffer.r_data)
				m.d.comb += length_buffer.r_en.eq(1)
				m.next = 'INSPECT_PACKET'

			# INSPECT_PACKET: Check that the new packet wouldn't overflow the available output FIFO space
			with m.State('INSPECT_PACKET'):
				m.d.usb += packet_transferred.eq(0)
				with m.If(data_buffer.w_level + packet_length + 2 > self.mem_size):
					m.next = 'OVERRUN'
				with m.Else():
					m.next = 'TRANSFER_PACKET'

			# TRANSFER_PACKET: Moves the captured packet between FIFOs, appending the length to the front
			with m.State('TRANSFER_PACKET'):
				# First, write the length in little endian
				with m.If(packet_transferred == 0):
					m.d.comb += [
						data_buffer.w_data.eq(packet_length[8:16]),
						data_buffer.w_en.eq(1),
					]
				with m.Elif(packet_transferred == 1):
					m.d.comb += [
						data_buffer.w_data.eq(packet_length[0:8]),
						data_buffer.w_en.eq(1),
					]
				# Then write the packet data byte for byte
				with m.Elif(packet_length != 0):
					m.d.comb += [
						data_buffer.w_data.eq(packet_buffer.r_data),
						packet_buffer.r_en.eq(1),
						data_buffer.w_en.eq(1),
					]
					m.d.usb += packet_length.eq(packet_length - 1)
				# If the packet size is now 0, we're done and can go back to idle
				with m.Else():
					m.next = 'IDLE'
				m.d.usb += packet_transferred.eq(packet_transferred + 1)

			# OVERRUN: handles the case where the new packet would overrun the buffer
			with m.State('OVERRUN'):
				# Latch on that we've had an overrun occur
				m.d.usb += self.overrun.eq(1)

				# Check there's space in the buffer to write an invalid packet size
				with m.If(data_buffer.w_level + 2 <= self.mem_size):
					m.next = 'CLEAR_OVERRUN'

			# CLEAR_OVERRUN: write the overrun marker into the buffer and clear packet_size bytes from the packet buffer
			with m.State('CLEAR_OVERRUN'):
				# Write the marker (0xffff)
				with m.If((packet_transferred == 0) | (packet_transferred == 1)):
					m.d.comb += [
						data_buffer.w_en.eq(1),
						data_buffer.w_data.eq(0xff),
					]
					m.d.usb += packet_transferred.eq(packet_transferred + 1)
				# Clear the packet out from the buffer
				with m.Elif(packet_length != 0):
					m.d.comb += packet_buffer.r_en.eq(1)
					m.d.usb += packet_length.eq(packet_length - 1)
				# We're done, return to idle
				with m.Else():
					m.next = 'IDLE'

		return m
