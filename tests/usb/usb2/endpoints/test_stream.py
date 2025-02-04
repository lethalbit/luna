# SPDX-License-Identifier: BSD-3-Clause

from random                                     import randint
from torii.sim                                  import Settle

from sol_usb.gateware.usb.usb2.endpoints.stream import (
	USBMultibyteStreamInEndpoint
)
from sol_usb.gateware.test                      import SolGatewareTestCase, usb_domain_test_case

class USBMultibyteStreamInEndpointTest(SolGatewareTestCase):
	SYNC_CLOCK_FREQUENCY = None
	USB_CLOCK_FREQUENCY  = 60e6

	FRAGMENT_UNDER_TEST = USBMultibyteStreamInEndpoint
	FRAGMENT_ARGUMENTS = {
		'byte_width': 3,
		'endpoint_number': 1,
		'max_packet_size': 64,
	}
	dut: USBMultibyteStreamInEndpoint

	@usb_domain_test_case
	def test_short_packet(self):
		dut = self.dut
		in_stream = dut.stream
		ep = dut.interface.tokenizer
		out_stream = dut.interface.tx

		self.assertEqual((yield in_stream.ready), 1)
		yield
		# Queue first block of data to EP
		yield in_stream.payload.eq(0xf00f55)
		yield in_stream.first.eq(1)
		yield in_stream.valid.eq(1)
		yield Settle()
		self.assertEqual((yield in_stream.ready), 1)
		self.assertEqual((yield out_stream.valid), 0)
		yield
		# Make sure it tells us that it can't take more now
		yield in_stream.first.eq(0)
		yield in_stream.valid.eq(0)
		yield Settle()
		self.assertEqual((yield in_stream.ready), 0)
		self.assertEqual((yield out_stream.valid), 0)
		# Wait for stream to become ready gain
		yield from self.wait_until(in_stream.ready)
		# Queue second block of data to EP
		yield in_stream.payload.eq(0x0ff0aa)
		yield in_stream.last.eq(1)
		yield in_stream.valid.eq(1)
		yield
		# Make sure it tells us that it can't take more now
		yield in_stream.last.eq(0)
		yield in_stream.valid.eq(0)
		yield Settle()
		self.assertEqual((yield in_stream.ready), 0)
		self.assertEqual((yield out_stream.valid), 0)
		yield from self.wait_until(in_stream.ready)
		yield
		# Arm EP for transmitting response data
		self.assertEqual((yield out_stream.valid), 0)
		yield ep.endpoint.eq(1)
		yield ep.is_in.eq(1)
		yield ep.ready_for_response.eq(1)
		yield
		yield ep.ready_for_response.eq(0)
		yield out_stream.ready.eq(1)
		self.assertEqual((yield out_stream.valid), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0x55)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 1)
		self.assertEqual((yield out_stream.last), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0x0f)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 0)
		self.assertEqual((yield out_stream.last), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0xf0)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 0)
		self.assertEqual((yield out_stream.last), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0xaa)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 0)
		self.assertEqual((yield out_stream.last), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0xf0)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 0)
		self.assertEqual((yield out_stream.last), 0)
		yield
		self.assertEqual((yield out_stream.payload), 0x0f)
		self.assertEqual((yield out_stream.valid), 1)
		self.assertEqual((yield out_stream.first), 0)
		self.assertEqual((yield out_stream.last), 1)
		yield out_stream.ready.eq(0)
		yield
		self.assertEqual((yield out_stream.valid), 0)
		yield dut.interface.handshakes_in.ack.eq(1)
		yield
		yield dut.interface.handshakes_in.ack.eq(0)

	def test_long_packet(self):
		self.domain = 'usb'
		self._ensure_clocks_present()
		dut = self.dut
		in_stream = dut.stream
		ep = dut.interface.tokenizer
		out_stream = dut.interface.tx

		# Construct 3 & a bit USB packets worth of data to send
		packet = [randint(0, 2**24 - 1) for _ in range(66)]

		def queue_data():
			yield from self.initialize_signals()
			self.assertEqual((yield in_stream.ready), 1)
			yield
			# Queue blocks of data to EP
			for idx, data in enumerate(packet):
				yield in_stream.payload.eq(data)
				yield in_stream.first.eq(idx == 0)
				yield in_stream.last.eq(idx == 65)
				yield in_stream.valid.eq(1)
				yield
				yield in_stream.first.eq(0)
				yield in_stream.last.eq(0)
				yield in_stream.valid.eq(0)
				yield
				yield from self.wait_until(in_stream.ready)
			yield

		def consume_packet(packet: list[int]):
			last = len(packet) - 1
			# Dequeue packet
			yield out_stream.ready.eq(1)
			for idx, byte in enumerate(packet):
				yield
				self.assertEqual((yield out_stream.payload), byte)
				self.assertEqual((yield out_stream.valid), 1)
				self.assertEqual((yield out_stream.first), idx == 0)
				self.assertEqual((yield out_stream.last), idx == last)
			yield out_stream.ready.eq(0)
			yield
			self.assertEqual((yield out_stream.valid), 0)
			yield ep.ready_for_response.eq(0)
			# Ack it
			yield dut.interface.handshakes_in.ack.eq(1)
			yield
			yield dut.interface.handshakes_in.ack.eq(0)
			yield

		def consume_data():
			packetBytes = [byte for data in packet for byte in data.to_bytes(3, byteorder = 'little')]

			# Wait a bit to see what the streaming interface does
			yield from self.advance_cycles(200)

			# Set up receiving data from this interface
			yield ep.endpoint.eq(1)
			yield ep.is_in.eq(1)
			for block in range(0, len(packetBytes), 64):
				yield ep.ready_for_response.eq(1)
				yield
				# Wait for the NAK signal to go low, indicating packet ready
				while (yield dut.interface.handshakes_out.nak) == 1:
					yield
				yield
				yield ep.ready_for_response.eq(0)
				# Dequeue packet
				yield from consume_packet(packetBytes[block:block + 64])
			yield

		assert self.USB_CLOCK_FREQUENCY is not None
		self.sim.add_sync_process(queue_data, domain = 'usb')
		self.sim.add_sync_process(consume_data, domain = 'usb')
		self.simulate(vcd_suffix = self.test_long_packet.__name__)
