# SPDX-License-Identifier: BSD-3-Clause

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
