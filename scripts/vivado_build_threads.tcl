# Executed inside synthesis/implementation worker processes.
# Four threads improve on the Windows default without maximizing RAM pressure.
set_param general.maxThreads 4
puts "BUILD_RESOURCE_SETTING: general.maxThreads=[get_param general.maxThreads]"
