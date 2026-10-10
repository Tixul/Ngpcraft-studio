"""GB Studio link events on the NGPC cable (docs/M6_LINK.md).

GBVM vm_sio.c (MIT, Copyright (c) 2020 Toxa, GBVM_LICENSE.txt) is kept: the
exchange state machine, the master (Link Join) sending first, the slave
(Link Host) answering, the VM waiting on the instruction until both packets
have crossed, Link Close ending an exchange without copying anything.

What changes is GBVM's sio.c / sio_a.s, the Game Boy serial port. The NGPC
cable is the TLCS-900 SC0 UART driven by BIOS calls (vectors 0x10..0x1A,
verified on a BIOS dump; ring TX 0x6C80 / RX 0x6CC0, 64 bytes, levels at
0x6D00 / 0x6D01), as in Over Rev's two-player mode (ngpc_link, from
NgpCraft_base_template/optional/ngpc_link). Calls follow the V3 idiom
(save_flash.c, text_split.c): bank-3 registers named explicitly, `swi 1`,
then `ei 0`.

The wire. GBVM sends [len][data] and has no recovery: one lost byte and both
consoles wait forever (measured here with a cable pulled out and plugged
back). The NGPC frame is A5 | seq | len | data | sum ^ 5A (ngpc_link's
framing plus a sequence number) with stop-and-wait recovery: the master sends
its packet again when no answer came after NGC_LINK_RESEND calls; a slave
that receives the packet it already answered sends its last answer again and
waits for the next one. A damaged frame is dropped (resync on A5). The pairs
of exchanges stay those of GBVM: packet n of one console meets packet n of
the other.
"""

LINK_HEAD = r"""#ifndef VM_REF_TO_PTR
#define VM_REF_TO_PTR(idx) (void *)(((idx) < 0) ? THIS->stack_ptr + (idx) : script_memory + (idx))
#endif
/* GBVM sio.h / vm_sio.c on the NGPC cable (docs/M6_LINK.md, ngpcraft/gbvm/link.py). */
#define LINK_MAX_PACKET_LENGTH 32u
#define LINK_MODE_NONE 0u
#define LINK_MODE_MASTER 1u
#define LINK_MODE_SLAVE 2u
#define PACKET_SEND_INIT 1u
#define PACKET_SEND_DONE 2u
#define PACKET_RECV_INIT 4u
#define PACKET_RECV_DONE 8u
#define EXCHANGE_STARTED 0u
#define EXCHANGE_COMPLETED (PACKET_SEND_INIT | PACKET_SEND_DONE | PACKET_RECV_INIT | PACKET_RECV_DONE)
static UBYTE link_operation_mode;
static UBYTE exchange_state;
static UBYTE ngc_link_open;

static void ngc_com_init(void)
{
    __asm("push xwa"); __asm("push xbc"); __asm("push xde"); __asm("push xhl");
    __asm("push xix"); __asm("push xiy"); __asm("push xiz");
    __asm("ldb rw3, 0x10");      /* COMINIT */
    __asm("swi 1");
    __asm("ldb rw3, 0x12");      /* COMRECIVESTART */
    __asm("swi 1");
    __asm("pop xiz"); __asm("pop xiy"); __asm("pop xix");
    __asm("pop xhl"); __asm("pop xde"); __asm("pop xbc"); __asm("pop xwa");
    __asm("ei 0");
}

"""

# Link Transfer only: the frame, the BIOS block calls, the recovery and
# vm_sio_exchange. The two buffers the asm names are public (a cc900 static
# may not keep its name).
LINK_EXCHANGE = r"""#define NGC_LINK_SYNC 0xA5u
#define NGC_LINK_SALT 0x5Au
#define NGC_LINK_RESEND 30u     /* calls (frames) without an answer before the master sends again */
#define NGC_COM_TX_COUNT (*(volatile UBYTE *)0x6D00)
#define NGC_COM_RX_COUNT (*(volatile UBYTE *)0x6D01)
static UBYTE link_packet[LINK_MAX_PACKET_LENGTH];
static UBYTE link_packet_received;
static UBYTE link_packet_sent;
/* Frame being read: state 0 sync, 1 sequence, 2 length, 3 data, 4 sum. */
static UBYTE ngc_link_rx_state, ngc_link_rx_seq, ngc_link_rx_len, ngc_link_rx_pos, ngc_link_rx_sum;
static UBYTE ngc_link_rx[LINK_MAX_PACKET_LENGTH];
static UBYTE ngc_link_got_seq;          /* sequence of the packet in link_packet */
static UBYTE ngc_link_seq;              /* master: packet in flight; slave: last packet answered */
static UBYTE ngc_link_wait;             /* master: calls since the packet was sent */
/* The last frame sent, kept to send it again. */
static UBYTE ngc_link_tx[LINK_MAX_PACKET_LENGTH + 4u];
static UBYTE ngc_link_tx_len;
/* BIOS block transfers: xhl3 = ngc_com_buf, rb3 = ngc_com_count. */
UBYTE ngc_com_buf[LINK_MAX_PACKET_LENGTH + 4u];
UBYTE ngc_com_count;

static void ngc_com_send(void)
{
    __asm("push xwa"); __asm("push xbc"); __asm("push xde"); __asm("push xhl");
    __asm("push xix"); __asm("push xiy"); __asm("push xiz");
    __asm("ld xhl, _ngc_com_buf");
    __asm("ld xhl3, xhl");
    __asm("ld a, (_ngc_com_count)");
    __asm("ld rb3, a");
    __asm("ldb rw3, 0x19");      /* COMCREATEBUFDATA */
    __asm("swi 1");
    __asm("ldb rw3, 0x11");      /* COMSENDSTART */
    __asm("swi 1");
    __asm("pop xiz"); __asm("pop xiy"); __asm("pop xix");
    __asm("pop xhl"); __asm("pop xde"); __asm("pop xbc"); __asm("pop xwa");
    __asm("ei 0");
}

static void ngc_com_receive(void)
{
    __asm("push xwa"); __asm("push xbc"); __asm("push xde"); __asm("push xhl");
    __asm("push xix"); __asm("push xiy"); __asm("push xiz");
    __asm("ld xhl, _ngc_com_buf");
    __asm("ld xhl3, xhl");
    __asm("ld a, (_ngc_com_count)");
    __asm("ld rb3, a");
    __asm("ldb rw3, 0x1A");      /* COMGETBUFDATA */
    __asm("swi 1");
    __asm("pop xiz"); __asm("pop xiy"); __asm("pop xix");
    __asm("pop xhl"); __asm("pop xde"); __asm("pop xbc"); __asm("pop xwa");
    __asm("ei 0");
}

/* sio.c on_SIO_receive: the peer's next packet into link_packet (one at a
 * time: a frame after it stays in the BIOS ring). A damaged frame is dropped. */
static void ngc_link_poll(void)
{
    UBYTE b;
    while (!link_packet_received && NGC_COM_RX_COUNT) {
        ngc_com_count = 1u;
        ngc_com_receive();
        b = ngc_com_buf[0];
        switch (ngc_link_rx_state) {
        case 0u:
            if (b == NGC_LINK_SYNC) ngc_link_rx_state = 1u;
            break;
        case 1u:
            ngc_link_rx_seq = b; ngc_link_rx_sum = b; ngc_link_rx_state = 2u;
            break;
        case 2u:
            if (b > LINK_MAX_PACKET_LENGTH) { ngc_link_rx_state = (b == NGC_LINK_SYNC) ? 1u : 0u; break; }
            ngc_link_rx_len = b; ngc_link_rx_pos = 0u; ngc_link_rx_sum = (UBYTE)(ngc_link_rx_sum + b);
            ngc_link_rx_state = b ? 3u : 4u;
            break;
        case 3u:
            ngc_link_rx[ngc_link_rx_pos++] = b;
            ngc_link_rx_sum = (UBYTE)(ngc_link_rx_sum + b);
            if (ngc_link_rx_pos == ngc_link_rx_len) ngc_link_rx_state = 4u;
            break;
        default:
            ngc_link_rx_state = 0u;
            if (b != (UBYTE)(ngc_link_rx_sum ^ NGC_LINK_SALT)) break;
            for (b = 0u; b < ngc_link_rx_len; ++b) link_packet[b] = ngc_link_rx[b];
            ngc_link_got_seq = ngc_link_rx_seq;
            link_packet_received = TRUE;
            break;
        }
    }
}

/* The kept frame into the BIOS ring; 0 when the ring has no room yet. */
static UBYTE ngc_link_resend(void)
{
    UBYTE i;
    if ((UBYTE)(64u - NGC_COM_TX_COUNT) < ngc_link_tx_len) return 0u;
    for (i = 0u; i < ngc_link_tx_len; ++i) ngc_com_buf[i] = ngc_link_tx[i];
    ngc_com_count = ngc_link_tx_len;
    ngc_com_send();
    return 1u;
}

/* sio.h SIO_send_async: link_packet framed with sequence seq. */
static UBYTE ngc_link_send(UBYTE len, UBYTE seq)
{
    UBYTE i, sum;
    if ((UBYTE)(64u - NGC_COM_TX_COUNT) < (UBYTE)(len + 4u)) return 0u;
    ngc_link_tx[0] = NGC_LINK_SYNC;
    ngc_link_tx[1] = seq;
    ngc_link_tx[2] = len;
    sum = (UBYTE)(seq + len);
    for (i = 0u; i < len; ++i) {
        ngc_link_tx[3u + i] = link_packet[i];
        sum = (UBYTE)(sum + link_packet[i]);
    }
    ngc_link_tx[3u + len] = (UBYTE)(sum ^ NGC_LINK_SALT);
    ngc_link_tx_len = (UBYTE)(len + 4u);
    ngc_link_resend();
    link_packet_sent = TRUE;
    return 1u;
}

static void ngc_link_copy(UBYTE * dst, const UBYTE * src, UBYTE len)
{
    while (len--) *dst++ = *src++;
}

static void vm_sio_exchange(SCRIPT_CTX * THIS, INT16 idxA, INT16 idxB, UBYTE len)
{
    INT16 * data;
    if (link_operation_mode == LINK_MODE_NONE) {
        exchange_state = EXCHANGE_COMPLETED;
        return;
    }
    if (exchange_state == EXCHANGE_COMPLETED) exchange_state = EXCHANGE_STARTED;
    if (len > LINK_MAX_PACKET_LENGTH) len = LINK_MAX_PACKET_LENGTH;
    switch (link_operation_mode) {
        case LINK_MODE_MASTER:
            if ((exchange_state & PACKET_SEND_INIT) == 0) {
                link_packet_sent = link_packet_received = FALSE;
                data = VM_REF_TO_PTR(idxA);
                ngc_link_copy(link_packet, (const UBYTE *)data, len);
                /* 1..255: 0 means "nothing answered yet" on the slave. */
                if (ngc_link_send(len, (UBYTE)(ngc_link_seq == 255u ? 1u : ngc_link_seq + 1u))) {
                    ngc_link_seq = ngc_link_tx[1];
                    ngc_link_wait = 0u;
                    exchange_state |= PACKET_SEND_INIT;
                }
            } else if ((exchange_state & PACKET_SEND_DONE) == 0) {
                if (link_packet_sent) exchange_state |= PACKET_SEND_DONE;
            } else if ((exchange_state & PACKET_RECV_INIT) == 0) {
                ngc_link_poll();
                /* An answer to an older packet (sent again) is not this one's. */
                if (link_packet_received && ngc_link_got_seq != ngc_link_seq) link_packet_received = FALSE;
                if (link_packet_received) exchange_state |= PACKET_RECV_INIT;
                else if (++ngc_link_wait >= NGC_LINK_RESEND && ngc_link_resend()) ngc_link_wait = 0u;
            } else if ((exchange_state & PACKET_RECV_DONE) == 0) {
                data = VM_REF_TO_PTR(idxB);
                ngc_link_copy((UBYTE *)data, link_packet, len);
                exchange_state |= PACKET_RECV_DONE;
            }
            break;
        case LINK_MODE_SLAVE:
            if ((exchange_state & PACKET_RECV_INIT) == 0) {
                link_packet_sent = link_packet_received = FALSE;
                exchange_state |= PACKET_RECV_INIT;
            } else if ((exchange_state & PACKET_RECV_DONE) == 0) {
                ngc_link_poll();
                if (link_packet_received && ngc_link_got_seq == ngc_link_seq) {
                    /* The packet already answered: the answer was lost, send it again. */
                    link_packet_received = FALSE;
                    ngc_link_resend();
                }
                if (link_packet_received) {
                    ngc_link_seq = ngc_link_got_seq;
                    data = VM_REF_TO_PTR(idxB);
                    ngc_link_copy((UBYTE *)data, link_packet, len);
                    exchange_state |= PACKET_RECV_DONE;
                }
            } else if ((exchange_state & PACKET_SEND_INIT) == 0) {
                data = VM_REF_TO_PTR(idxA);
                ngc_link_copy(link_packet, (const UBYTE *)data, len);
                if (ngc_link_send(len, ngc_link_seq)) exchange_state |= PACKET_SEND_INIT;
            } else if ((exchange_state & PACKET_SEND_DONE) == 0) {
                if (link_packet_sent) exchange_state |= PACKET_SEND_DONE;
            }
            break;
    }
    /* re-run next time if transfer is not completed */
    if (exchange_state != EXCHANGE_COMPLETED)
        THIS->PC -= (INSTRUCTION_SIZE + sizeof(idxA) + sizeof(idxB) + sizeof(len)), THIS->waitable = TRUE;
}

"""

LINK_INIT = r"""/* sio.c SIO_init; vm_sio.c exchange_state = EXCHANGE_COMPLETED. */
static void ngc_link_init(void)
{
    link_operation_mode = LINK_MODE_NONE;
    exchange_state = EXCHANGE_COMPLETED;
    ngc_link_open = 0u;
    NGC_LINK_RESET
}

"""

LINK_SET_MODE = r"""static void vm_sio_set_mode(SCRIPT_CTX * THIS, UBYTE mode)
{
    (void)THIS;
    exchange_state = EXCHANGE_COMPLETED;
    /* SIO_set_mode: the slave starts receiving; the NGPC UART receives in
     * both roles once the BIOS channel is open. A new session numbers its
     * packets from 1 again. */
    link_operation_mode = mode;
    if (mode != LINK_MODE_NONE && !ngc_link_open) {
        ngc_com_init();
        ngc_link_open = 1u;
    }
    NGC_LINK_RESET
}

"""

RESET = ("link_packet_received = FALSE;\n    link_packet_sent = FALSE;\n    ngc_link_rx_state = 0u;\n"
         "    ngc_link_seq = 0u;\n    ngc_link_wait = 0u;")

# vm.i: bytes after the opcode, the handler's fields reversed.
DECODERS = {
    "VM_SIO_SET_MODE": (("MODE",), ("vm_sio_set_mode",), "vm_sio_set_mode(ctx, *ctx->PC++);"),
    "VM_SIO_EXCHANGE": (("SIZE", "DEST", "SOUR"), ("vm_sio_exchange",),
                        "n = *ctx->PC++; a = ngc_read_word(ctx); b = ngc_read_word(ctx); "
                        "vm_sio_exchange(ctx, (INT16)b, (INT16)a, n);"),
}
LINK_FUNCTIONS = ("vm_sio_set_mode", "vm_sio_exchange")


def source(exchange):
    """Link C for gbvm.c: the exchange part only with Link Transfer."""
    reset = RESET if exchange else "/* no Link Transfer: nothing else to reset */"
    return (LINK_HEAD + (LINK_EXCHANGE if exchange else "") +
            LINK_INIT.replace("NGC_LINK_RESET", reset) + LINK_SET_MODE.replace("NGC_LINK_RESET", reset))
