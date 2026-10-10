void ngc_backdrop(short value)
{
    *(volatile unsigned short *)0x83E0 = (unsigned short)value & 0x0FFFu;
    *(volatile unsigned char *)0x8118 = 0x80u;
}
