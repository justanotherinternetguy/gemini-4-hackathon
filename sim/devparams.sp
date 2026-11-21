* Check device-parameter access for saturation checks
.lib models/sky130_slim.lib.spice tt
XM1 d g 0 0 sky130_fd_pr__nfet_01v8 W=2 L=0.5 nf=1
Vd d 0 0.9
Vg g 0 0.7
.control
op
print @m.xm1.msky130_fd_pr__nfet_01v8[id] @m.xm1.msky130_fd_pr__nfet_01v8[vds] @m.xm1.msky130_fd_pr__nfet_01v8[vdsat] @m.xm1.msky130_fd_pr__nfet_01v8[vth] @m.xm1.msky130_fd_pr__nfet_01v8[gm] @m.xm1.msky130_fd_pr__nfet_01v8[gds]
quit
.endc
.end
