def main():
    try:
        directory_path = get_user_input("Enter the directory path: ")
        search_pattern = get_user_input("Enter the search pattern: ")
        
        results = call_search_function(directory_path, search_pattern)
        
        if results:
            print_results(results)
        else:
            print("No files found matching the search pattern.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()