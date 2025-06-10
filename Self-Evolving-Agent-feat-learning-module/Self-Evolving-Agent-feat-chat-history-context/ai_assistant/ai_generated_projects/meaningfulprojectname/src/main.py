import config
import utils

def main():
    try:
        config.load_config()
        utils.initialize()
        register_event_listeners()
        while True:
            utils.process_events()
            if utils.check_shutdown():
                break
    except Exception as e:
        utils.log_error(f"Critical error: {str(e)}")
        utils.cleanup()

if __name__ == "__main__":
    main()